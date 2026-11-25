"""One attendee conversation for each formal report/dispute, including old records."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.orders.models import Order
from app.platform.operations_models import EventReport, OrderDispute
from app.support.access import history, notify
from app.support.models import SupportCase, SupportMessage


def conversation_status(status):
    return (
        "resolved"
        if status in {"resolved", "dismissed"}
        else "in_progress"
        if status == "under_review"
        else "open"
    )


def ensure_conversation(db, record):
    report = isinstance(record, EventReport)
    column = SupportCase.event_report_id if report else SupportCase.order_dispute_id
    existing = db.scalar(select(SupportCase).where(column == record.id))
    if existing:
        return existing
    order = None if report else db.get(Order, record.order_id)
    requester_id = record.reporter_id if report else record.requester_id
    case = SupportCase(
        requester_id=requester_id,
        flow="attendee",
        event_id=record.event_id if report else order.event_id,
        order_id=order.id if order else None,
        event_report_id=record.id if report else None,
        order_dispute_id=None if report else record.id,
        category="report_event" if report else "payment",
        subject="Report event" if report else "Payment problem",
        status=conversation_status(record.status),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )
    try:
        # The unique link also protects simultaneous startup/backfill workers.
        with db.begin_nested():
            db.add(case)
            db.flush()
            db.add(
                SupportMessage(
                    case_id=case.id,
                    sender_id=requester_id,
                    body=record.reason,
                    created_at=record.created_at,
                )
            )
            db.flush()
    except IntegrityError:
        existing = db.scalar(select(SupportCase).where(column == record.id))
        if existing is None:
            raise
        return existing
    return case


def backfill_conversations(db):
    for model, column in (
        (EventReport, SupportCase.event_report_id),
        (OrderDispute, SupportCase.order_dispute_id),
    ):
        for record in db.scalars(
            select(model).where(~select(SupportCase.id).where(column == model.id).exists())
        ):
            ensure_conversation(db, record)


def sync_decision(db, record, actor):
    case = ensure_conversation(db, record)
    # Formal record locks are acquired first by the caller.
    case = db.scalar(
        select(SupportCase)
        .where(SupportCase.id == case.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    old = case.status
    case.status = conversation_status(record.status)
    case.updated_at = datetime.now(UTC)
    if old != case.status:
        history(db, case, actor, "status", old, case.status)
    db.add(
        SupportMessage(
            case_id=case.id,
            sender_id=actor.id,
            body=f"Platform review: {record.status.replace('_', ' ')}. {record.resolution}",
        )
    )
    notify(
        db,
        case,
        actor,
        "report_status" if isinstance(record, EventReport) else "dispute_status",
        "Platform review update",
        case.subject,
    )
