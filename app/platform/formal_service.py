"""Shared validation for reports/disputes opened through chat or legacy clients."""

from fastapi import HTTPException
from sqlalchemy import select

from app.events.models import Event
from app.events.service import audit
from app.orders.models import Order
from app.platform.operations_models import EventReport, OrderDispute
from app.platform.operations_service import require_access


def meaningful_reason(reason):
    if len(reason.strip()) < 3:
        raise HTTPException(422, detail="Please provide a meaningful reason")
    return reason.strip()


def open_report(db, event_id, reason, user):
    reason = meaningful_reason(reason)
    event = db.scalar(select(Event).where(Event.id == event_id).with_for_update())
    require_access(db, event, user)
    if event.status not in {"published", "suspended"} or event.organizer_id == user.id:
        raise HTTPException(409, detail="This event cannot be reported by this account")
    if db.scalar(
        select(EventReport.id).where(
            EventReport.event_id == event.id,
            EventReport.reporter_id == user.id,
            EventReport.status.in_(["open", "under_review"]),
        )
    ):
        raise HTTPException(
            409,
            detail=(
                "You already have an active report for this event. "
                "Continue its support conversation."
            ),
        )
    row = EventReport(event_id=event.id, reporter_id=user.id, reason=reason)
    db.add(row)
    db.flush()
    audit(
        db,
        user,
        event,
        "report.created",
        "report",
        row.id,
        "Event report submitted for administrator review",
    )
    return row


def open_dispute(db, order_id, reason, user):
    reason = meaningful_reason(reason)
    initial = db.get(Order, order_id)
    if not initial or initial.user_id != user.id:
        raise HTTPException(404, detail="Order not found")
    db.scalar(select(Event).where(Event.id == initial.event_id).with_for_update())
    order = db.scalar(
        select(Order)
        .where(Order.id == order_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if order.status != "confirmed" or order.total_minor <= 0:
        raise HTTPException(409, detail="Disputes require a confirmed paid order")
    if db.scalar(
        select(OrderDispute.id).where(
            OrderDispute.order_id == order.id, OrderDispute.status.in_(["open", "under_review"])
        )
    ):
        raise HTTPException(
            409,
            detail=(
                "An active dispute already exists for this order. "
                "Continue its support conversation."
            ),
        )
    row = OrderDispute(order_id=order.id, requester_id=user.id, reason=reason)
    db.add(row)
    db.flush()
    audit(
        db,
        user,
        db.get(Event, order.event_id),
        "dispute.created",
        "dispute",
        row.id,
        "Simulated payment dispute opened",
    )
    # Keep the organizer's payout hold visible without exposing the private chat.
    from app.users.models import Notification

    event = db.get(Event, order.event_id)
    db.add(
        Notification(
            user_id=event.organizer_id,
            kind="dispute_opened",
            title=event.title,
            message="A payment dispute is under platform review. Payout is on hold.",
            data={"event_id": str(event.id)},
        )
    )
    return row
