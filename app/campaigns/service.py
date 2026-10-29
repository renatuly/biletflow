"""Promo-code rules. All discounts are computed and enforced on the server."""

import io
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

import qrcode
from fastapi import HTTPException
from PIL import Image, ImageDraw, ImageFont
from qrcode.constants import ERROR_CORRECT_H
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.campaigns.models import Campaign
from app.core.config import get_settings
from app.events.models import Event
from app.orders.models import Order, OrderStatus
from app.tickets.models import TicketType

CAMPAIGN_QR_COLOR = "#3a1fb8"


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def normalize_code(code: str) -> str:
    return code.strip().upper()


def held_redemptions(db: Session, campaign: Campaign) -> int:
    """Pending (unexpired) checkouts that already reserved one redemption of this campaign."""
    return (
        db.scalar(
            select(func.count(Order.id)).where(
                Order.campaign_id == campaign.id, Order.status == OrderStatus.PENDING
            )
        )
        or 0
    )


def campaign_state(campaign: Campaign, now: datetime, held: int = 0) -> str:
    if not campaign.enabled:
        return "disabled"
    if now < utc(campaign.valid_from):
        return "scheduled"
    if now >= utc(campaign.valid_until):
        return "expired"
    if campaign.redemption_count + held >= campaign.max_redemptions:
        return "exhausted"
    return "active"


STATE_MESSAGES = {
    "disabled": "Promo code {code} has been disabled by the organizer.",
    "scheduled": "Promo code {code} is not active yet.",
    "expired": "Promo code {code} has expired.",
    "exhausted": "Promo code {code} has reached its redemption limit.",
}


def state_message(campaign: Campaign, state: str) -> str | None:
    template = STATE_MESSAGES.get(state)
    return template.format(code=campaign.code) if template else None


def applies_to(campaign: Campaign, ticket_type: TicketType) -> bool:
    allowed = campaign.applicable_ticket_type_ids
    return not allowed or str(ticket_type.id) in allowed


@dataclass
class PromoQuote:
    campaign: Campaign
    discount_minor: int
    # Discount allocated to each ticket type, so order items record their share.
    item_discounts: dict[UUID, int] = field(default_factory=dict)


def apply_promo(
    db: Session,
    event: Event,
    priced: list[tuple[TicketType, int]],
    code: str,
    now: datetime,
    lock: bool = False,
) -> PromoQuote:
    """Validate a code against the selected tickets and compute the discount.

    Raises 422 with an attendee-readable message when the code cannot be used.
    """
    statement = select(Campaign).where(
        Campaign.event_id == event.id, Campaign.code == normalize_code(code)
    )
    campaign = db.scalar(statement.with_for_update() if lock else statement)
    if not campaign:
        raise HTTPException(
            422, detail=f"Promo code {normalize_code(code)} is not valid for this event."
        )
    state = campaign_state(campaign, now, held_redemptions(db, campaign))
    if state != "active":
        raise HTTPException(422, detail=state_message(campaign, state))
    eligible = [
        (ticket_type, quantity)
        for ticket_type, quantity in priced
        if applies_to(campaign, ticket_type) and ticket_type.price_minor > 0
    ]
    eligible_subtotal = sum(t.price_minor * q for t, q in eligible)
    if not eligible_subtotal:
        raise HTTPException(
            422, detail=f"Promo code {campaign.code} does not apply to the selected tickets."
        )
    discount = (
        eligible_subtotal * campaign.discount_value // 100
        if campaign.discount_type == "percentage"
        else min(eligible_subtotal, campaign.discount_value)
    )
    allocations: dict[UUID, int] = {}
    remaining = discount
    for index, (ticket_type, quantity) in enumerate(eligible):
        share = (
            remaining
            if index == len(eligible) - 1
            else discount * ticket_type.price_minor * quantity // eligible_subtotal
        )
        allocations[ticket_type.id] = share
        remaining -= share
    return PromoQuote(campaign, discount, allocations)


def redeem(db: Session, campaign_id: UUID) -> bool:
    """Atomically consume one redemption; False when the limit is already reached."""
    result = db.execute(
        update(Campaign)
        .where(Campaign.id == campaign_id, Campaign.redemption_count < Campaign.max_redemptions)
        .values(redemption_count=Campaign.redemption_count + 1)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


def campaign_link(campaign: Campaign) -> str:
    return f"{get_settings().frontend_url}/campaign/{campaign.opaque_token}"


def campaign_qr_png(campaign: Campaign) -> bytes:
    """Promotional QR, deliberately unlike the black admission QR: colored and labelled."""
    code = qrcode.QRCode(error_correction=ERROR_CORRECT_H, box_size=10, border=2)
    code.add_data(campaign_link(campaign))
    code.make(fit=True)
    qr_image = code.make_image(fill_color=CAMPAIGN_QR_COLOR, back_color="white").convert("RGB")
    padding, band = 30, 70
    width = qr_image.width + padding * 2
    canvas = Image.new("RGB", (width, qr_image.height + band * 2 + padding), "white")
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, width, band), fill=CAMPAIGN_QR_COLOR)
    draw.rectangle((0, 0, width - 1, canvas.height - 1), outline=CAMPAIGN_QR_COLOR, width=6)
    title = ImageFont.load_default(size=28)
    caption = ImageFont.load_default(size=22)
    draw.text((width / 2, band / 2), "PROMO OFFER", fill="white", font=title, anchor="mm")
    canvas.paste(qr_image, (padding, band))
    footer_y = band + qr_image.height + padding / 2
    draw.text(
        (width / 2, footer_y + 15),
        f"Code: {campaign.code}",
        fill="black",
        font=caption,
        anchor="mm",
    )
    draw.text(
        (width / 2, footer_y + 45),
        "Not an admission ticket",
        fill=CAMPAIGN_QR_COLOR,
        font=caption,
        anchor="mm",
    )
    stream = io.BytesIO()
    canvas.save(stream, format="PNG")
    return stream.getvalue()
