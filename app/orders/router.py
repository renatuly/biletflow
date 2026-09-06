from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import current_user
from app.events.models import Event
from app.events.service import audit, require_event_manager
from app.orders.models import Order, OrderItem, Payment, Refund
from app.orders.schemas import CheckoutCreate, OrderRead, PaymentSimulation, RefundRequest
from app.orders.service import complete_checkout, create_checkout
from app.tickets.models import Ticket, TicketStatus, TicketType
from app.users.models import Notification, User

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
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404, detail="Order not found")
    event = require_event_manager(db, order.event_id, user)
    if order.status != "confirmed":
        raise HTTPException(409, detail="Only confirmed orders can be refunded")
    payment = db.scalar(
        select(Payment).where(Payment.order_id == order.id, Payment.status == "succeeded")
    )
    refund = Refund(
        order_id=order.id,
        payment_id=payment.id if payment else None,
        initiated_by=user.id,
        amount_minor=order.total_minor,
        reason=data.reason,
    )
    db.add(refund)
    items = list(db.scalars(select(OrderItem).where(OrderItem.order_id == order.id)).all())
    for item in items:
        ticket_type = db.get(TicketType, item.ticket_type_id)
        ticket_type.refunded_count += item.quantity
        ticket_type.sold_count -= item.quantity
    for ticket in db.scalars(select(Ticket).where(Ticket.order_item_id.in_([i.id for i in items]))):
        ticket.status = TicketStatus.REFUNDED
    order.status = "refunded"
    db.add(
        Notification(
            user_id=order.user_id,
            kind="refund_completion",
            title="Refund completed",
            message="Your demonstration refund was completed.",
        )
    )
    audit(
        db, user, event, "order.refunded", "order", order.id, "Full demonstration refund completed"
    )
    db.commit()
    db.refresh(refund)
    return {
        "id": str(refund.id),
        "status": refund.status,
        "amount_minor": refund.amount_minor,
        "currency": order.currency,
        "is_simulation": True,
    }
