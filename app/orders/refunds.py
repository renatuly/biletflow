"""Full-order demo refunds shared by order actions and support approvals."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.events.models import Event
from app.events.service import audit
from app.orders.models import Order, OrderItem, Payment, Refund
from app.support.models import SupportCase, SupportMessage
from app.tickets.models import CheckInRecord, Ticket, TicketStatus, TicketType
from app.users.models import Notification, User


def require_refund_owner(db: Session, order: Order, user: User) -> Event:
    event = db.get(Event, order.event_id)
    if event.organizer_id != user.id and "platform_admin" not in user.roles:
        raise HTTPException(403, detail="Only the event organizer can approve refunds")
    return event


def complete_refund(db: Session, order_id: UUID, reason: str, user: User) -> Refund:
    order = db.scalar(
        select(Order)
        .where(Order.id == order_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not order:
        raise HTTPException(404, detail="Order not found")
    event = require_refund_owner(db, order, user)
    if order.status != "confirmed" or order.total_minor <= 0:
        raise HTTPException(409, detail="Only confirmed paid orders can be refunded")
    payment = db.scalar(
        select(Payment).where(Payment.order_id == order.id, Payment.status == "succeeded")
    )
    refund = Refund(
        order_id=order.id,
        payment_id=payment.id if payment else None,
        initiated_by=user.id,
        amount_minor=order.total_minor,
        reason=reason,
        status="succeeded",
        is_simulation=True,
    )
    db.add(refund)
    items = list(db.scalars(select(OrderItem).where(OrderItem.order_id == order.id)))
    for item in items:
        kind = db.scalar(
            select(TicketType)
            .where(TicketType.id == item.ticket_type_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        kind.refunded_count += item.quantity
        kind.sold_count = max(0, kind.sold_count - item.quantity)
    tickets = list(
        db.scalars(
            select(Ticket)
            .where(Ticket.order_item_id.in_([i.id for i in items]))
            .order_by(Ticket.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    for ticket in tickets:
        ticket.status = TicketStatus.REFUNDED
    records = db.scalars(
        select(CheckInRecord).where(
            CheckInRecord.ticket_id.in_([t.id for t in tickets]),
            CheckInRecord.reversed_at.is_(None),
        )
    )
    for record in records:
        record.reversed_at = datetime.now(UTC)
        record.reversed_by = user.id
    order.status = "refunded"
    cases = db.scalars(
        select(SupportCase).where(
            SupportCase.order_id == order.id, SupportCase.category == "refund"
        )
    )
    for case in cases:
        if case.status != "resolved":
            from app.support.access import history

            history(db, case, user, "status", case.status, "resolved")
            case.status = "resolved"
            case.updated_at = datetime.now(UTC)
            db.add(
                SupportMessage(
                    case_id=case.id,
                    sender_id=user.id,
                    body=(
                        "Refund approved. The full demonstration refund is complete "
                        "and all tickets in this order are invalid."
                    ),
                )
            )
    if order.user_id:
        db.add(
            Notification(
                user_id=order.user_id,
                kind="refund_completion",
                title="Refund approved",
                message=(
                    "Your full demonstration refund was completed. Your refund case is solved "
                    "and the order's tickets are no longer valid."
                ),
            )
        )
    audit(
        db,
        user,
        event,
        "order.refunded",
        "order",
        order.id,
        "Full demonstration refund approved; linked refund cases resolved",
    )
    db.flush()
    return refund
