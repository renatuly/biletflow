"""Event operations with server-authoritative permissions and retained audit records."""

from collections import defaultdict
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.campaigns.models import Campaign
from app.core.database import get_db
from app.core.dependencies import current_user, require_role
from app.events.models import AuditLog, Event
from app.events.service import audit, require_event_manager
from app.orders.models import Order, OrderItem, Refund
from app.tickets.models import Ticket, TicketStatus, TicketType
from app.users.models import Notification, User, UserRole

router = APIRouter(tags=["event management"])


class Reason(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class Control(Reason):
    action: str = Field(pattern="^(suspend_event|restore_event|suspend_sales|restore_sales)$")


def owner(db, event_id, user):
    event = require_event_manager(db, event_id, user)
    if event.organizer_id != user.id and "platform_admin" not in user.roles:
        raise HTTPException(
            403, detail="Only the event organizer or administrator can manage orders"
        )
    return event


@router.get("/events/{event_id}/activity")
def activity(
    event_id: UUID,
    date_from: date | None = None,
    date_to: date | None = None,
    action: str | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    require_event_manager(db, event_id, user)
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, detail="Invalid date range")
    rows = db.scalars(
        select(AuditLog)
        .where(AuditLog.event_id == event_id)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
    ).all()
    result = []
    for row in rows:
        if (
            date_from
            and row.created_at.date() < date_from
            or date_to
            and row.created_at.date() > date_to
        ):
            continue
        if action and not row.action.startswith(action + "."):
            continue
        actor = db.get(User, row.actor_id) if row.actor_id else None
        result.append(
            dict(
                id=str(row.id),
                created_at=row.created_at,
                action=row.action,
                actor=row.data.get(
                    "actor_name", (actor.full_name or actor.email) if actor else "System"
                ),
                actor_roles=row.data.get("actor_roles", actor.roles if actor else []),
                entity_type=row.entity_type,
                entity_id=row.entity_id,
                description=row.description,
            )
        )
    return result


@router.get("/events/{event_id}/analytics")
def analytics(
    event_id: UUID,
    date_from: date | None = None,
    date_to: date | None = None,
    ticket_type_id: UUID | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    event = owner(db, event_id, user)
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, detail="Invalid date range")
    kinds = list(db.scalars(select(TicketType).where(TicketType.event_id == event_id)))
    if ticket_type_id and ticket_type_id not in [t.id for t in kinds]:
        raise HTTPException(404, detail="Ticket type not found for this event")
    selected = [t for t in kinds if not ticket_type_id or t.id == ticket_type_id]
    orders = list(db.scalars(select(Order).where(Order.event_id == event_id)))
    selected_orders = [
        o
        for o in orders
        if (not date_from or o.created_at.date() >= date_from)
        and (not date_to or o.created_at.date() <= date_to)
    ]
    successful = [o for o in selected_orders if o.status in ("confirmed", "refunded")]
    items = (
        list(
            db.scalars(select(OrderItem).where(OrderItem.order_id.in_([o.id for o in successful])))
        )
        if successful
        else []
    )
    tickets = list(db.scalars(select(Ticket).where(Ticket.event_id == event_id)))
    refunds = (
        list(
            db.scalars(
                select(Refund).where(
                    Refund.order_id.in_([o.id for o in successful]), Refund.status == "succeeded"
                )
            )
        )
        if successful
        else []
    )
    refund_by_order = defaultdict(int)
    for r in refunds:
        refund_by_order[r.order_id] += r.amount_minor
    gross = discounts = fees = refunded = issued = checked = 0
    series = defaultdict(lambda: dict(tickets=0, revenue_minor=0))
    campaigns = {
        c.id: dict(name=c.name, code=c.code, redemptions=0, tickets=0, revenue_minor=0)
        for c in db.scalars(select(Campaign).where(Campaign.event_id == event_id))
    }
    comparison = []
    for kind in selected:
        ktickets = [
            t
            for t in tickets
            if t.ticket_type_id == kind.id and t.order_item_id in [i.id for i in items]
        ]
        valid = [t for t in ktickets if t.status in ("valid", "checked_in")]
        checked += sum(t.status == "checked_in" for t in valid)
        issued += len(valid)
        comparison.append(
            dict(
                id=str(kind.id),
                name=kind.name,
                capacity=kind.quantity,
                tickets=len(valid),
                checked_in=sum(t.status == "checked_in" for t in valid),
            )
        )
    for o in successful:
        oi = [
            i
            for i in items
            if i.order_id == o.id and (not ticket_type_id or i.ticket_type_id == ticket_type_id)
        ]
        if not oi:
            continue
        subtotal = sum(i.unit_price_minor * i.quantity for i in oi)
        discount = (
            o.discount_minor
            if not ticket_type_id
            else round(o.discount_minor * subtotal / max(1, o.subtotal_minor))
        )
        # Allocate order discounts, fees and refunds for the ticket-type filter.
        all_items = [i for i in items if i.order_id == o.id]
        weight = sum(i.quantity for i in oi) / max(1, sum(i.quantity for i in all_items))
        fee = round(o.processing_fee_minor * weight)
        refund = round(refund_by_order[o.id] * weight)
        total = subtotal - discount + fee
        gross += subtotal
        discounts += discount
        fees += fee
        refunded += refund
        day = str(o.created_at.date())
        series[day]["tickets"] += sum(i.quantity for i in oi)
        series[day]["revenue_minor"] += total - refund
        if o.campaign_id in campaigns:
            c = campaigns[o.campaign_id]
            c["redemptions"] += 1
            c["tickets"] += sum(i.quantity for i in oi)
            c["revenue_minor"] += total - refund
    capacity = selected[0].quantity if ticket_type_id else event.capacity
    inventory_active = sum(
        t.status in ("valid", "checked_in")
        for t in tickets
        if not ticket_type_id or t.ticket_type_id == ticket_type_id
    )
    return dict(
        capacity=capacity,
        tickets_sold=inventory_active,
        cohort_tickets=issued,
        tickets_remaining=max(0, capacity - inventory_active),
        percentage_sold=round(100 * inventory_active / capacity, 1) if capacity else 0,
        gross_sales_minor=gross,
        discounts_minor=discounts,
        processing_fees_minor=fees,
        refunds_minor=refunded,
        net_revenue_minor=gross - discounts + fees - refunded,
        checked_in=checked,
        absent=issued - checked,
        check_in_percentage=round(100 * checked / issued, 1) if issued else 0,
        currency="KZT",
        is_simulation=True,
        sales_series=[dict(date=d, **series[d]) for d in sorted(series)],
        ticket_types=comparison,
        available_ticket_types=[dict(id=str(t.id), name=t.name) for t in kinds],
        campaigns=list(campaigns.values()),
    )


@router.post("/admin/events/{event_id}/controls")
def controls(
    event_id: UUID,
    data: Control,
    admin: User = Depends(require_role(UserRole.PLATFORM_ADMIN)),
    db: Session = Depends(get_db),
):
    event = db.scalar(
        select(Event)
        .where(Event.id == event_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not event:
        raise HTTPException(404, detail="Event not found")
    if not data.reason.strip():
        raise HTTPException(422, detail="A reason is required")
    if event.status in ("cancelled", "completed"):
        raise HTTPException(409, detail="Closed events cannot be suspended or restored")
    if data.action == "suspend_event":
        if event.status == "suspended":
            raise HTTPException(409, detail="Event is already suspended")
        previous = event.status
        event.status = "suspended"
    elif data.action == "restore_event":
        if event.status != "suspended":
            raise HTTPException(409, detail="Event is not suspended")
        record = db.scalar(
            select(AuditLog)
            .where(AuditLog.event_id == event.id, AuditLog.action == "operations.suspend_event")
            .order_by(AuditLog.created_at.desc())
            .limit(1)
        )
        event.status = record.data.get("previous_status", "draft") if record else "draft"
    elif data.action == "suspend_sales":
        if event.paid_sales_status != "active":
            raise HTTPException(409, detail="Only active paid sales can be suspended")
        event.paid_sales_status = "suspended"
    else:
        if event.paid_sales_status != "suspended":
            raise HTTPException(409, detail="Paid sales are not suspended")
        from app.events.paid_applications import application, fingerprint

        review = application(db, event)
        event.paid_sales_status = (
            "active"
            if review
            and review.status == "approved"
            and review.snapshot_hash == fingerprint(db, event)
            else "not_ready"
        )
    record = AuditLog(
        actor_id=admin.id,
        event_id=event.id,
        action="operations." + data.action,
        entity_type="event",
        entity_id=str(event.id),
        description=data.reason.strip(),
        data={
            "actor_name": admin.full_name or admin.email,
            "actor_roles": list(admin.roles),
            **({"previous_status": previous} if data.action == "suspend_event" else {}),
        },
    )
    db.add(record)
    db.add(
        Notification(
            user_id=event.organizer_id,
            kind="event_operation",
            title=event.title,
            message=data.action.replace("_", " ") + ": " + data.reason.strip(),
        )
    )
    db.commit()
    return dict(status=event.status, paid_sales_status=event.paid_sales_status)


@router.post("/orders/{order_id}/cancel-registration")
def cancel_registration(
    order_id: UUID, data: Reason, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    order = db.scalar(
        select(Order)
        .where(Order.id == order_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not order:
        raise HTTPException(404, detail="Order not found")
    event = owner(db, order.event_id, user)
    if not data.reason.strip():
        raise HTTPException(422, detail="A reason is required")
    items = list(db.scalars(select(OrderItem).where(OrderItem.order_id == order.id)))
    types = {
        t.id: t
        for t in db.scalars(
            select(TicketType)
            .where(TicketType.id.in_([i.ticket_type_id for i in items]))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    }
    if (
        not items
        or order.status != "confirmed"
        or order.total_minor != 0
        or any(t.kind != "free" for t in types.values())
    ):
        raise HTTPException(409, detail="Only confirmed free registrations can be cancelled here")
    tickets = list(
        db.scalars(
            select(Ticket)
            .where(Ticket.order_item_id.in_([i.id for i in items]))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    if any(t.status == "checked_in" for t in tickets):
        raise HTTPException(
            409, detail="Reverse admission before cancelling a checked-in registration"
        )
    for t in tickets:
        t.status = TicketStatus.CANCELLED
    for i in items:
        types[i.ticket_type_id].sold_count = max(0, types[i.ticket_type_id].sold_count - i.quantity)
    order.status = "cancelled"
    audit(db, user, event, "order.registration_cancelled", "order", order.id, data.reason.strip())
    if order.user_id:
        db.add(
            Notification(
                user_id=order.user_id,
                kind="registration_cancelled",
                title=event.title,
                message="Your free registration was cancelled: " + data.reason.strip(),
            )
        )
    db.commit()
    return dict(status="cancelled")


@router.get("/admin/operations/orders")
def operational_orders(
    admin: User = Depends(require_role(UserRole.PLATFORM_ADMIN)), db: Session = Depends(get_db)
):
    from app.core.config import get_settings
    from app.orders.models import Payment

    rows = []
    for order in db.scalars(select(Order).order_by(Order.created_at.desc())):
        event = db.get(Event, order.event_id)
        payments = list(db.scalars(select(Payment).where(Payment.order_id == order.id)))
        refunds = list(db.scalars(select(Refund).where(Refund.order_id == order.id)))
        rows.append(
            dict(
                id=str(order.id),
                event_id=str(order.event_id),
                event_title=event.title,
                attendee_name=order.attendee_name,
                attendee_email=order.attendee_email,
                status=order.status,
                total_minor=order.total_minor,
                currency=order.currency,
                created_at=order.created_at,
                payments=[
                    dict(
                        reference=p.provider_reference, status=p.status, amount_minor=p.amount_minor
                    )
                    for p in payments
                ],
                refunds=[
                    dict(status=r.status, amount_minor=r.amount_minor, reason=r.reason)
                    for r in refunds
                ],
            )
        )
    settings = get_settings()
    return dict(
        orders=rows,
        activation_fee_minor=settings.app_activation_fee_minor,
        processing_fee_bps=settings.app_processing_fee_bps,
        is_simulation=True,
    )


@router.post("/admin/users/{user_id}/restore")
def restore_user(
    user_id: UUID,
    data: Reason,
    admin: User = Depends(require_role(UserRole.PLATFORM_ADMIN)),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, detail="User not found")
    if not data.reason.strip():
        raise HTTPException(422, detail="A reason is required")
    user.is_active = True
    audit(db, admin, None, "user.restored", "user", user.id, data.reason.strip())
    db.commit()
    return dict(status="active")
