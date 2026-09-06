import secrets
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.campaigns.models import Campaign, PromoRedemption
from app.core.config import get_settings
from app.core.security import create_qr_credential
from app.events.models import Event
from app.orders.models import Order, OrderItem, OrderStatus, Payment
from app.orders.schemas import CheckoutCreate
from app.tickets.models import Ticket, TicketType
from app.users.models import Notification, User


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def create_checkout(db: Session, data: CheckoutCreate, user: User) -> Order:
    now = datetime.now(UTC)
    event = db.get(Event, data.event_id)
    if not event or event.status != "published":
        raise HTTPException(409, detail="Event is not available")
    if not (utc(event.registration_opens_at) <= now <= utc(event.registration_closes_at)):
        raise HTTPException(409, detail="Registration is closed")
    requested_ids = [item.ticket_type_id for item in data.items]
    types = list(
        db.scalars(
            select(TicketType).where(TicketType.id.in_(requested_ids)).with_for_update()
        ).all()
    )
    by_id = {item.id: item for item in types}
    subtotal = 0
    for request_item in data.items:
        ticket_type = by_id.get(request_item.ticket_type_id)
        if not ticket_type or ticket_type.event_id != event.id or ticket_type.is_hidden:
            raise HTTPException(404, detail="Ticket type not found")
        if not (utc(ticket_type.sales_start) <= now <= utc(ticket_type.sales_end)):
            raise HTTPException(409, detail="Ticket sales are closed")
        if request_item.quantity > ticket_type.max_per_order:
            raise HTTPException(422, detail="Per-order ticket limit exceeded")
        if (
            ticket_type.sold_count + ticket_type.reserved_count + request_item.quantity
            > ticket_type.quantity
        ):
            raise HTTPException(409, detail="TICKET_INVENTORY_EXHAUSTED")
        if ticket_type.kind == "paid" and event.paid_sales_status != "active":
            raise HTTPException(409, detail="Paid sales are not active")
        subtotal += ticket_type.price_minor * request_item.quantity
    campaign = None
    discount = 0
    if data.promo_code:
        campaign = db.scalar(
            select(Campaign)
            .where(Campaign.event_id == event.id, Campaign.code == data.promo_code.upper())
            .with_for_update()
        )
        if (
            not campaign
            or not campaign.enabled
            or not (utc(campaign.valid_from) <= now <= utc(campaign.valid_until))
            or campaign.redemption_count >= campaign.max_redemptions
        ):
            raise HTTPException(409, detail="Promo code is invalid, expired, or exhausted")
        discount = (
            subtotal * campaign.discount_value // 100
            if campaign.discount_type == "percentage"
            else min(subtotal, campaign.discount_value)
        )
    settings = get_settings()
    fee = (subtotal - discount) * settings.app_processing_fee_bps // 10_000 if subtotal else 0
    order = Order(
        event_id=event.id,
        user_id=user.id,
        attendee_name=data.attendee_name,
        attendee_email=data.attendee_email.lower(),
        subtotal_minor=subtotal,
        discount_minor=discount,
        processing_fee_minor=fee,
        total_minor=subtotal - discount + fee,
        expires_at=now + timedelta(minutes=settings.app_ticket_hold_minutes),
        campaign_id=campaign.id if campaign else None,
    )
    db.add(order)
    db.flush()
    for request_item in data.items:
        ticket_type = by_id[request_item.ticket_type_id]
        ticket_type.reserved_count += request_item.quantity
        db.add(
            OrderItem(
                order_id=order.id,
                ticket_type_id=ticket_type.id,
                quantity=request_item.quantity,
                unit_price_minor=ticket_type.price_minor,
            )
        )
    db.commit()
    db.refresh(order)
    return order


def complete_checkout(db: Session, order: Order, user: User, outcome: str) -> Order:
    now = datetime.now(UTC)
    order = db.scalar(select(Order).where(Order.id == order.id).with_for_update())
    if order.status == OrderStatus.CONFIRMED:
        return order
    if order.status != OrderStatus.PENDING or utc(order.expires_at) < now:
        raise HTTPException(409, detail="Checkout session expired or closed")
    items = list(db.scalars(select(OrderItem).where(OrderItem.order_id == order.id)).all())
    type_ids = [item.ticket_type_id for item in items]
    types = {
        item.id: item
        for item in db.scalars(
            select(TicketType).where(TicketType.id.in_(type_ids)).with_for_update()
        ).all()
    }
    if outcome == "failure":
        for item in items:
            types[item.ticket_type_id].reserved_count -= item.quantity
        order.status = OrderStatus.PAYMENT_FAILED
        db.add(
            Payment(
                order_id=order.id,
                provider_reference=f"sim_{secrets.token_hex(8)}",
                status="failed",
                amount_minor=order.total_minor,
            )
        )
        db.add(
            Notification(
                user_id=user.id,
                kind="payment_failure",
                title="Payment failed",
                message="Your simulated payment failed.",
            )
        )
        db.commit()
        return order
    payment = Payment(
        order_id=order.id,
        provider_reference=f"sim_{secrets.token_hex(8)}",
        status="succeeded",
        amount_minor=order.total_minor,
    )
    db.add(payment)
    for item in items:
        ticket_type = types[item.ticket_type_id]
        ticket_type.reserved_count -= item.quantity
        ticket_type.sold_count += item.quantity
        for _ in range(item.quantity):
            ticket = Ticket(
                order_item_id=item.id,
                event_id=order.event_id,
                ticket_type_id=item.ticket_type_id,
                owner_id=user.id,
                attendee_name=order.attendee_name,
                attendee_email=order.attendee_email,
                qr_credential="pending",
            )
            db.add(ticket)
            db.flush()
            ticket.qr_credential = create_qr_credential(str(ticket.id))
    if order.campaign_id:
        campaign = db.scalar(
            select(Campaign).where(Campaign.id == order.campaign_id).with_for_update()
        )
        if campaign.redemption_count >= campaign.max_redemptions:
            raise HTTPException(409, detail="Promo redemption limit reached")
        campaign.redemption_count += 1
        db.add(
            PromoRedemption(
                campaign_id=campaign.id, order_id=order.id, discount_minor=order.discount_minor
            )
        )
    order.status = OrderStatus.CONFIRMED
    db.add(
        Notification(
            user_id=user.id,
            kind="ticket_delivery",
            title="Tickets issued",
            message="Your BiletFlow tickets are ready.",
        )
    )
    db.commit()
    db.refresh(order)
    return order
