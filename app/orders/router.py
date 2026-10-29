from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import current_user
from app.core.email import queue
from app.events.models import Event, Venue
from app.events.service import require_event_manager
from app.notifications.emails import (
    order_confirmation_email,
    payment_failed_email,
    refund_email,
)
from app.orders.models import Order, OrderStatus
from app.orders.refunds import complete_refund
from app.orders.schemas import (
    CheckoutCreate,
    OrderRead,
    PaymentSimulation,
    QuoteRead,
    QuoteRequest,
    RefundRequest,
)
from app.orders.service import complete_checkout, create_checkout, order_tickets, price_checkout
from app.users.models import User

router = APIRouter(tags=["orders", "checkout"])


@router.post("/promo-codes/validate", response_model=QuoteRead)
def quote_checkout(
    data: QuoteRequest, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    """Price a selection (optionally with a promo code) without reserving inventory."""
    pricing = price_checkout(
        db, data.event_id, data.items, data.promo_code, user, datetime.now(UTC)
    )
    return QuoteRead(
        subtotal_minor=pricing.subtotal_minor,
        discount_minor=pricing.discount_minor,
        processing_fee_minor=pricing.processing_fee_minor,
        total_minor=pricing.total_minor,
        promo_code=pricing.promo.campaign.code if pricing.promo else None,
        campaign_name=pricing.promo.campaign.name if pricing.promo else None,
    )


@router.post("/checkout/sessions", response_model=OrderRead, status_code=201)
async def start_checkout(
    data: CheckoutCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None),
):
    return create_checkout(db, data, user, idempotency_key)


@router.get("/checkout/sessions/{order_id}", response_model=OrderRead)
def read_checkout(
    order_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    order = db.get(Order, order_id)
    if not order or order.user_id != user.id:
        raise HTTPException(404, detail="Checkout not found")
    return order


@router.post("/checkout/sessions/{order_id}/complete", response_model=OrderRead)
def finish_checkout(
    order_id: UUID,
    data: PaymentSimulation,
    background: BackgroundTasks,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    order = db.get(Order, order_id)
    if not order or order.user_id != user.id:
        raise HTTPException(404, detail="Checkout not found")
    already_confirmed = order.status == OrderStatus.CONFIRMED
    order = complete_checkout(db, order, user, data.outcome)
    event = db.get(Event, order.event_id)
    if order.status == OrderStatus.CONFIRMED and not already_confirmed:
        venue = db.get(Venue, event.venue_id)
        queue(background, order_confirmation_email(order, event, venue, order_tickets(db, order)))
    elif order.status == OrderStatus.PAYMENT_FAILED and order.total_minor > 0:
        queue(background, payment_failed_email(order, event))
    return order


@router.post("/orders/{order_id}/resend-confirmation", status_code=202)
def resend_confirmation(
    order_id: UUID,
    background: BackgroundTasks,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    order = db.get(Order, order_id)
    if not order or order.user_id != user.id:
        raise HTTPException(404, detail="Order not found")
    if order.status != OrderStatus.CONFIRMED:
        raise HTTPException(409, detail="Only confirmed orders have tickets to send")
    event = db.get(Event, order.event_id)
    venue = db.get(Venue, event.venue_id)
    queue(background, order_confirmation_email(order, event, venue, order_tickets(db, order)))
    return {"message": f"Tickets sent to {order.attendee_email}"}


@router.get("/orders", response_model=list[OrderRead])
def my_orders(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return list(
        db.scalars(
            select(Order).where(Order.user_id == user.id).order_by(Order.created_at.desc())
        ).all()
    )


@router.get("/orders/{order_id}", response_model=OrderRead)
def read_order(order_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404, detail="Order not found")
    event = db.get(Event, order.event_id)
    if (
        order.user_id != user.id
        and event.organizer_id != user.id
        and "platform_admin" not in user.roles
    ):
        raise HTTPException(403, detail="Not authorized")
    return order


@router.get("/events/{event_id}/orders", response_model=list[OrderRead])
def event_orders(event_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_event_manager(db, event_id, user)
    return list(
        db.scalars(
            select(Order).where(Order.event_id == event_id).order_by(Order.created_at.desc())
        ).all()
    )


@router.post("/orders/{order_id}/refunds", status_code=201)
def refund_order(
    order_id: UUID,
    data: RefundRequest,
    background: BackgroundTasks,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if not data.reason.strip():
        raise HTTPException(422, detail="Refund reason is required")
    refund = complete_refund(db, order_id, data.reason.strip(), user)
    order = db.get(Order, order_id)
    db.commit()
    db.refresh(refund)
    queue(background, refund_email(order, db.get(Event, order.event_id), refund.amount_minor))
    return {
        "id": str(refund.id),
        "status": refund.status,
        "amount_minor": refund.amount_minor,
        "currency": order.currency,
        "is_simulation": True,
    }


@router.post("/checkout/sessions/{order_id}/cancel", response_model=OrderRead)
def cancel_checkout(
    order_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    order = db.get(Order, order_id)
    if not order or order.user_id != user.id:
        raise HTTPException(404, detail="Checkout not found")
    db.scalar(select(Event).where(Event.id == order.event_id).with_for_update())
    order = db.scalar(
        select(Order)
        .where(Order.id == order_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    from datetime import UTC, datetime

    from app.orders.reservations import release
    from app.platform.operations_service import utc

    if order.status == "pending":
        release(
            db,
            order,
            "expired" if utc(order.expires_at) <= datetime.now(UTC) else "cancelled",
            user,
        )
        db.commit()
    elif order.status not in ("cancelled", "expired"):
        raise HTTPException(409, detail="This checkout can no longer be abandoned")
    return order
