from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import current_user
from app.events.models import Event
from app.events.service import require_event_manager
from app.orders.models import Order
from app.orders.refunds import complete_refund
from app.orders.schemas import CheckoutCreate, OrderRead, PaymentSimulation, RefundRequest
from app.orders.service import complete_checkout, create_checkout
from app.users.models import User

router = APIRouter(tags=["orders", "checkout"])


@router.post("/checkout/sessions", response_model=OrderRead, status_code=201)
async def start_checkout(
    data: CheckoutCreate, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    return create_checkout(db, data, user)


@router.get("/checkout/sessions/{order_id}", response_model=OrderRead)
def read_checkout(
    order_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    order = db.get(Order, order_id)
    if not order or order.user_id != user.id:
        raise HTTPException(404, detail="Checkout not found")
    return order


@router.post("/checkout/sessions/{order_id}/complete", response_model=OrderRead)
async def finish_checkout(
    order_id: UUID,
    data: PaymentSimulation,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    order = db.get(Order, order_id)
    if not order or order.user_id != user.id:
        raise HTTPException(404, detail="Checkout not found")
    return complete_checkout(db, order, user, data.outcome)


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
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if not data.reason.strip():
        raise HTTPException(422, detail="Refund reason is required")
    refund = complete_refund(db, order_id, data.reason.strip(), user)
    order = db.get(Order, order_id)
    db.commit()
    db.refresh(refund)
    return {
        "id": str(refund.id),
        "status": refund.status,
        "amount_minor": refund.amount_minor,
        "currency": order.currency,
        "is_simulation": True,
    }
