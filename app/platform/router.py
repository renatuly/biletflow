from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.campaigns.models import Campaign
from app.campaigns.service import campaign_state, held_redemptions
from app.core.database import get_db
from app.core.dependencies import current_user, require_role
from app.events.models import Event
from app.events.schemas import EventRead
from app.events.service import audit, require_event_manager
from app.orders.models import Order, OrderItem, Refund
from app.tickets.models import Ticket, TicketStatus, TicketType
from app.users.models import User, UserRole
from app.users.schemas import UserRead

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
    """Exact campaign performance from confirmed and refunded BiletFlow orders."""
    require_event_manager(db, event_id, user)
    campaigns = db.scalars(
        select(Campaign).where(Campaign.event_id == event_id).order_by(Campaign.created_at)
    ).all()
    tickets = (
        select(OrderItem.order_id, func.sum(OrderItem.quantity).label("quantity"))
        .group_by(OrderItem.order_id)
        .subquery()
    )
    rows = db.execute(
        select(
            Order.campaign_id,
            Order.status,
            func.count(Order.id),
            func.coalesce(func.sum(tickets.c.quantity), 0),
            func.coalesce(func.sum(Order.subtotal_minor), 0),
            func.coalesce(func.sum(Order.discount_minor), 0),
        )
        .join(tickets, tickets.c.order_id == Order.id)
        .where(
            Order.event_id == event_id,
            Order.campaign_id.is_not(None),
            Order.status.in_(["confirmed", "refunded"]),
        )
        .group_by(Order.campaign_id, Order.status)
    ).all()
    totals: dict = {}
    for campaign_id, status, orders, quantity, gross, discount in rows:
        entry = totals.setdefault(
            campaign_id, {"confirmed": (0, 0, 0, 0), "refunded": (0, 0, 0, 0)}
        )
        entry[status] = (orders, quantity, gross, discount)
    result = []
    now = datetime.now(UTC)
    for campaign in campaigns:
        entry = totals.get(campaign.id, {"confirmed": (0, 0, 0, 0), "refunded": (0, 0, 0, 0)})
        orders, quantity, gross, discount = entry["confirmed"]
        refunded_orders, _, refunded_gross, refunded_discount = entry["refunded"]
        result.append(
            {
                "id": str(campaign.id),
                "name": campaign.name,
                "code": campaign.code,
                "state": campaign_state(campaign, now, held_redemptions(db, campaign)),
                "visits": campaign.visit_count,
                "redemptions": campaign.redemption_count,
                "maximum": campaign.max_redemptions,
                "orders": orders,
                "tickets_sold": quantity,
                "gross_revenue_minor": gross,
                "discount_minor": discount,
                "net_revenue_minor": gross - discount,
                "refunded_orders": refunded_orders,
                "refunded_minor": refunded_gross - refunded_discount,
                "conversion_rate": round(orders * 100 / campaign.visit_count, 2)
                if campaign.visit_count
                else None,
                "currency": "KZT",
                "is_simulation": True,
            }
        )
    return result


@router.get("/organizer/analytics/ticket-types")
def ticket_type_analytics(
    event_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    require_event_manager(db, event_id, user)
    sums = {
        row[0]: row[1:]
        for row in db.execute(
            select(
                OrderItem.ticket_type_id,
                func.coalesce(func.sum(OrderItem.quantity * OrderItem.unit_price_minor), 0),
                func.coalesce(func.sum(OrderItem.discount_minor), 0),
            )
            .join(Order, Order.id == OrderItem.order_id)
            .where(Order.event_id == event_id, Order.status == "confirmed")
            .group_by(OrderItem.ticket_type_id)
        ).all()
    }
    types = db.scalars(select(TicketType).where(TicketType.event_id == event_id)).all()
    result = []
    for ticket_type in types:
        gross, discount = sums.get(ticket_type.id, (0, 0))
        result.append(
            {
                "ticket_type_id": str(ticket_type.id),
                "name": ticket_type.name,
                "capacity": ticket_type.quantity,
                "tickets_sold": ticket_type.sold_count,
                "reserved": ticket_type.reserved_count,
                "refunded": ticket_type.refunded_count,
                "gross_sales_minor": gross,
                "discounts_minor": discount,
                "net_revenue_minor": gross - discount,
                "currency": "KZT",
            }
        )
    return result


@router.get("/admin/users", response_model=list[UserRead])
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


@router.get("/admin/events", response_model=list[EventRead])
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


@router.get("/admin/campaigns")
def admin_campaigns(
    q: str | None = None,
    admin: User = Depends(require_role(UserRole.PLATFORM_ADMIN)),
    db: Session = Depends(get_db),
):
    """Promotional-code activity across the platform, most recent first."""
    statement = select(Campaign, Event.title).join(Event, Event.id == Campaign.event_id)
    if q:
        statement = statement.where(Campaign.code.ilike(f"%{q}%") | Event.title.ilike(f"%{q}%"))
    now = datetime.now(UTC)
    return [
        {
            "id": str(campaign.id),
            "event_id": str(campaign.event_id),
            "event_title": title,
            "name": campaign.name,
            "code": campaign.code,
            "discount_type": campaign.discount_type,
            "discount_value": campaign.discount_value,
            "state": campaign_state(campaign, now, held_redemptions(db, campaign)),
            "visits": campaign.visit_count,
            "redemptions": campaign.redemption_count,
            "maximum": campaign.max_redemptions,
            "created_at": campaign.created_at,
        }
        for campaign, title in db.execute(
            statement.order_by(Campaign.created_at.desc()).limit(200)
        ).all()
    ]
