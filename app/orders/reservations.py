"""Idempotent hold release; event -> order -> ticket-type locking order."""

from datetime import UTC, datetime

from sqlalchemy import select

from app.events.models import AuditLog, Event
from app.orders.models import Order, OrderItem
from app.tickets.models import TicketType


def release(db, order, status, actor=None):
    if order.status != "pending":
        return False
    items = list(db.scalars(select(OrderItem).where(OrderItem.order_id == order.id)))
    types = {
        t.id: t
        for t in db.scalars(
            select(TicketType)
            .where(TicketType.id.in_([i.ticket_type_id for i in items]))
            .order_by(TicketType.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    }
    for item in items:
        types[item.ticket_type_id].reserved_count = max(
            0, types[item.ticket_type_id].reserved_count - item.quantity
        )
    order.status = status
    db.add(
        AuditLog(
            actor_id=actor.id if actor else None,
            event_id=order.event_id,
            action="order." + status,
            entity_type="order",
            entity_id=str(order.id),
            description="Checkout hold released: " + status,
        )
    )
    db.flush()
    return True


def expire_event(db, event_id, now=None):
    now = now or datetime.now(UTC)
    db.scalar(select(Event).where(Event.id == event_id).with_for_update())
    rows = db.scalars(
        select(Order)
        .where(Order.event_id == event_id, Order.status == "pending", Order.expires_at <= now)
        .order_by(Order.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return sum(release(db, order, "expired") for order in rows)


def sweep():
    from app.core.database import SessionLocal

    with SessionLocal() as db:
        ids = list(
            db.scalars(
                select(Order.event_id)
                .where(Order.status == "pending", Order.expires_at <= datetime.now(UTC))
                .distinct()
            )
        )
    for event_id in ids:
        with SessionLocal() as db:
            expire_event(db, event_id)
            db.commit()
