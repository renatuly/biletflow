import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.campaigns.models import PromoRedemption
from app.campaigns.service import PromoQuote, apply_promo, redeem
from app.core.config import get_settings
from app.core.security import create_qr_credential
from app.events.models import Event
from app.orders.models import Order, OrderItem, OrderStatus, Payment
from app.orders.schemas import CheckoutCreate, CheckoutItem
from app.tickets.models import Ticket, TicketType
from app.users.models import Notification, User


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def release_order(db: Session, order: Order, status: OrderStatus) -> None:
    """Return a pending order's reserved inventory (and its promo hold) to the pool."""
    for item in db.scalars(select(OrderItem).where(OrderItem.order_id == order.id)):
        ticket_type = db.get(TicketType, item.ticket_type_id)
        ticket_type.reserved_count = max(0, ticket_type.reserved_count - item.quantity)
    order.status = status


def release_expired_orders(db: Session, now: datetime) -> None:
    """Expire abandoned checkouts lazily so inventory and promo holds are not leaked."""
    pending = db.scalars(select(Order).where(Order.status == OrderStatus.PENDING)).all()
    expired = [order for order in pending if utc(order.expires_at) < now]
    for order in expired:
        release_order(db, order, OrderStatus.EXPIRED)
    if expired:
        db.commit()


@dataclass
class Pricing:
    event: Event
    priced: list[tuple[TicketType, int]]
    subtotal_minor: int
    promo: PromoQuote | None
    processing_fee_minor: int

    @property
    def discount_minor(self) -> int:
        return self.promo.discount_minor if self.promo else 0

    @property
    def total_minor(self) -> int:
        return self.subtotal_minor - self.discount_minor + self.processing_fee_minor


def price_checkout(
    db: Session,
    event_id,
    items: list[CheckoutItem],
    promo_code: str | None,
    now: datetime,
    lock: bool = False,
) -> Pricing:
    event = db.get(Event, event_id)
    if not event or event.status != "published":
        raise HTTPException(409, detail="Event is not available")
    if not (utc(event.registration_opens_at) <= now <= utc(event.registration_closes_at)):
        raise HTTPException(409, detail="Registration is closed")
    if len({item.ticket_type_id for item in items}) != len(items):
        raise HTTPException(422, detail="Each ticket type may appear only once per order")
    statement = select(TicketType).where(TicketType.id.in_([i.ticket_type_id for i in items]))
    by_id = {t.id: t for t in db.scalars(statement.with_for_update() if lock else statement).all()}
    priced: list[tuple[TicketType, int]] = []
    for request_item in items:
        ticket_type = by_id.get(request_item.ticket_type_id)
        if not ticket_type or ticket_type.event_id != event.id or ticket_type.is_hidden:
            raise HTTPException(404, detail="Ticket type not found")
        if not (utc(ticket_type.sales_start) <= now <= utc(ticket_type.sales_end)):
            raise HTTPException(409, detail=f"Sales for {ticket_type.name} are closed")
        if request_item.quantity > ticket_type.max_per_order:
            raise HTTPException(
                422, detail=f"You can buy at most {ticket_type.max_per_order} {ticket_type.name}"
            )
        if (
            ticket_type.sold_count + ticket_type.reserved_count + request_item.quantity
            > ticket_type.quantity
        ):
            raise HTTPException(409, detail="TICKET_INVENTORY_EXHAUSTED")
        if ticket_type.kind == "paid" and event.paid_sales_status != "active":
            raise HTTPException(409, detail="Paid sales are not active")
        priced.append((ticket_type, request_item.quantity))
    subtotal = sum(t.price_minor * q for t, q in priced)
    promo = apply_promo(db, event, priced, promo_code, now, lock) if promo_code else None
    discounted = subtotal - (promo.discount_minor if promo else 0)
    fee = discounted * get_settings().app_processing_fee_bps // 10_000 if discounted else 0
    return Pricing(event, priced, subtotal, promo, fee)


def create_checkout(db: Session, data: CheckoutCreate, user: User) -> Order:
    now = datetime.now(UTC)
    release_expired_orders(db, now)
    pricing = price_checkout(db, data.event_id, data.items, data.promo_code, now, lock=True)
    settings = get_settings()
    order = Order(
        event_id=pricing.event.id,
        user_id=user.id,
        attendee_name=data.attendee_name,
        attendee_email=data.attendee_email.lower(),
        subtotal_minor=pricing.subtotal_minor,
        discount_minor=pricing.discount_minor,
        processing_fee_minor=pricing.processing_fee_minor,
        total_minor=pricing.total_minor,
        expires_at=now + timedelta(minutes=settings.app_ticket_hold_minutes),
        campaign_id=pricing.promo.campaign.id if pricing.promo else None,
    )
    db.add(order)
    db.flush()
    for ticket_type, quantity in pricing.priced:
        ticket_type.reserved_count += quantity
        db.add(
            OrderItem(
                order_id=order.id,
                ticket_type_id=ticket_type.id,
                quantity=quantity,
                unit_price_minor=ticket_type.price_minor,
                discount_minor=pricing.promo.item_discounts.get(ticket_type.id, 0)
                if pricing.promo
                else 0,
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
    if order.status == OrderStatus.PENDING and utc(order.expires_at) < now:
        release_order(db, order, OrderStatus.EXPIRED)
        db.commit()
    if order.status != OrderStatus.PENDING:
        raise HTTPException(409, detail="Checkout session expired or closed")
    items = list(db.scalars(select(OrderItem).where(OrderItem.order_id == order.id)).all())
    types = {
        item.id: item
        for item in db.scalars(
            select(TicketType)
            .where(TicketType.id.in_([item.ticket_type_id for item in items]))
            .with_for_update()
        ).all()
    }
    if outcome == "failure":
        release_order(db, order, OrderStatus.PAYMENT_FAILED)
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
    # Consume the promo redemption first so the limit can never be exceeded by issued tickets.
    if order.campaign_id and not redeem(db, order.campaign_id):
        release_order(db, order, OrderStatus.CANCELLED)
        db.commit()
        raise HTTPException(409, detail="The promo code reached its redemption limit")
    db.add(
        Payment(
            order_id=order.id,
            provider_reference=f"sim_{secrets.token_hex(8)}",
            status="succeeded",
            amount_minor=order.total_minor,
        )
    )
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
                qr_credential=f"pending-{secrets.token_hex(16)}",
            )
            db.add(ticket)
            db.flush()
            ticket.qr_credential = create_qr_credential(str(ticket.id))
    if order.campaign_id:
        db.add(
            PromoRedemption(
                campaign_id=order.campaign_id,
                order_id=order.id,
                discount_minor=order.discount_minor,
            )
        )
    order.status = OrderStatus.CONFIRMED
    db.add(
        Notification(
            user_id=user.id,
            kind="ticket_delivery",
            title="Tickets issued",
            message="Your BiletFlow tickets are ready and were sent to your email.",
        )
    )
    db.commit()
    db.refresh(order)
    return order


def order_tickets(db: Session, order: Order) -> list[tuple[Ticket, TicketType]]:
    rows = db.execute(
        select(Ticket, TicketType)
        .join(OrderItem, Ticket.order_item_id == OrderItem.id)
        .join(TicketType, Ticket.ticket_type_id == TicketType.id)
        .where(OrderItem.order_id == order.id)
        .order_by(Ticket.created_at)
    ).all()
    return [(ticket, ticket_type) for ticket, ticket_type in rows]
