from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.campaigns.models import Campaign
from app.core.database import get_db
from app.core.dependencies import current_user, require_role
from app.events.models import Event
from app.events.service import audit, require_event_manager
from app.orders.models import Order, Refund
from app.tickets.models import Ticket, TicketStatus, TicketType
from app.users.models import User, UserRole

router = APIRouter(tags=["analytics", "administration"])


@router.get("/organizer/analytics/summary")
def analytics_summary(
    event_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    event = require_event_manager(db, event_id, user)
    capacity = (
        db.scalar(select(func.sum(TicketType.quantity)).where(TicketType.event_id == event.id)) or 0
    )
    sold = (
        db.scalar(
            select(func.count(Ticket.id)).where(
                Ticket.event_id == event.id,
                Ticket.status.in_([TicketStatus.VALID, TicketStatus.CHECKED_IN]),
            )
        )
        or 0
    )
    checked_in = (
        db.scalar(
            select(func.count(Ticket.id)).where(
                Ticket.event_id == event.id, Ticket.status == TicketStatus.CHECKED_IN
            )
        )
        or 0
    )
    revenue = db.execute(
        select(
            func.coalesce(func.sum(Order.subtotal_minor), 0),
            func.coalesce(func.sum(Order.discount_minor), 0),
            func.coalesce(func.sum(Order.total_minor), 0),
        ).where(Order.event_id == event.id, Order.status == "confirmed")
    ).one()
    refunds = (
        db.scalar(
            select(func.coalesce(func.sum(Refund.amount_minor), 0))
            .join(Order, Refund.order_id == Order.id)
            .where(Order.event_id == event.id, Refund.status == "succeeded")
        )
        or 0
    )
    return {
        "event_id": str(event.id),
        "capacity": capacity,
        "tickets_sold": sold,
        "tickets_remaining": max(0, capacity - sold),
        "percentage_sold": round(sold * 100 / capacity, 2) if capacity else 0,
        "gross_sales_minor": revenue[0],
        "discounts_minor": revenue[1],
        "refunds_minor": refunds,
        "net_revenue_minor": revenue[2] - refunds,
        "currency": "KZT",
        "checked_in": checked_in,
        "absent": sold - checked_in,
        "check_in_percentage": round(checked_in * 100 / sold, 2) if sold else 0,
        "is_simulation": True,
    }


@router.get("/organizer/analytics/sales-series")
def sales_series(event_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_event_manager(db, event_id, user)
    day = func.date(Order.created_at)
    rows = db.execute(
        select(day, func.count(Order.id), func.sum(Order.total_minor))
        .where(Order.event_id == event_id, Order.status == "confirmed")
        .group_by(day)
        .order_by(day)
    ).all()
    return [
        {"date": str(row[0]), "orders": row[1], "revenue_minor": row[2], "currency": "KZT"}
        for row in rows
    ]


@router.get("/organizer/analytics/campaigns")
def campaign_analytics(
    event_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    require_event_manager(db, event_id, user)
    campaigns = db.scalars(select(Campaign).where(Campaign.event_id == event_id)).all()
    return [
        {
            "id": str(c.id),
            "name": c.name,
            "code": c.code,
            "redemptions": c.redemption_count,
            "maximum": c.max_redemptions,
        }
        for c in campaigns
    ]


@router.get("/admin/users")
def admin_users(
    q: str | None = None,
    admin: User = Depends(require_role(UserRole.PLATFORM_ADMIN)),
    db: Session = Depends(get_db),
):
    statement = select(User)
    if q:
        statement = statement.where(User.email.ilike(f"%{q}%"))
    return list(db.scalars(statement.limit(100)).all())


@router.post("/admin/users/{user_id}/suspend")
def suspend_user(
    user_id: UUID,
    admin: User = Depends(require_role(UserRole.PLATFORM_ADMIN)),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(404, detail="User not found")
    if target.id == admin.id:
        raise HTTPException(409, detail="Cannot suspend current administrator")
    target.is_active = False
    audit(db, admin, None, "user.suspended", "user", target.id, "User suspended")
    db.commit()
    return {"status": "suspended"}


@router.get("/admin/events")
def admin_events(
    q: str | None = None,
    admin: User = Depends(require_role(UserRole.PLATFORM_ADMIN)),
    db: Session = Depends(get_db),
):
    statement = select(Event)
    if q:
        statement = statement.where(Event.title.ilike(f"%{q}%"))
    return list(db.scalars(statement.limit(100)).all())


@router.post("/admin/events/{event_id}/suspend")
def suspend_event(
    event_id: UUID,
    admin: User = Depends(require_role(UserRole.PLATFORM_ADMIN)),
    db: Session = Depends(get_db),
):
    event = db.get(Event, event_id)
    if not event:
        raise HTTPException(404, detail="Event not found")
    event.paid_sales_status = "suspended"
    audit(
        db,
        admin,
        event,
        "event.suspended",
        "event",
        event.id,
        "Event paid sales suspended by platform",
    )
    db.commit()
    return {"status": "suspended"}


@router.get("/admin/reports/operations")
def operations(
    admin: User = Depends(require_role(UserRole.PLATFORM_ADMIN)), db: Session = Depends(get_db)
):
    return {
        "users": db.scalar(select(func.count(User.id))) or 0,
        "events": db.scalar(select(func.count(Event.id))) or 0,
        "orders": db.scalar(select(func.count(Order.id))) or 0,
        "tickets": db.scalar(select(func.count(Ticket.id))) or 0,
        "currency": "KZT",
        "financial_data_is_simulated": True,
    }
