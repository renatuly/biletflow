import secrets
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.campaigns.models import Campaign
from app.campaigns.service import (
    campaign_link,
    campaign_qr_png,
    campaign_state,
    held_redemptions,
    normalize_code,
    state_message,
    utc,
)
from app.core.database import get_db
from app.core.dependencies import current_user
from app.events.models import Event
from app.events.service import audit, require_event_manager
from app.tickets.models import TicketType
from app.users.models import User

router = APIRouter(tags=["campaigns"])

CODE_PATTERN = r"^[A-Za-z0-9_-]{3,40}$"


class CampaignCreate(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    code: str = Field(pattern=CODE_PATTERN)
    discount_type: str = Field(pattern="^(percentage|fixed_kzt)$")
    # Percentage points for "percentage"; KZT minor units (tiyn) for "fixed_kzt".
    discount_value: int = Field(gt=0)
    valid_from: datetime
    valid_until: datetime
    max_redemptions: int = Field(gt=0, le=1_000_000)
    applicable_ticket_type_ids: list[UUID] | None = None

    @field_validator("code")
    @classmethod
    def upper_code(cls, value: str) -> str:
        return normalize_code(value)

    @model_validator(mode="after")
    def validate_values(self):
        if utc(self.valid_until) <= utc(self.valid_from):
            raise ValueError("valid_until must be after valid_from")
        if self.discount_type == "percentage" and self.discount_value > 100:
            raise ValueError("percentage discounts must be between 1 and 100")
        return self


class CampaignUpdate(BaseModel):
    """Code and discount are immutable so existing attribution stays truthful."""

    name: str | None = Field(default=None, min_length=2, max_length=150)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    max_redemptions: int | None = Field(default=None, gt=0, le=1_000_000)
    enabled: bool | None = None
    applicable_ticket_type_ids: list[UUID] | None = None


class CampaignRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    event_id: UUID
    name: str
    code: str
    opaque_token: str
    discount_type: str
    discount_value: int
    valid_from: datetime
    valid_until: datetime
    max_redemptions: int
    redemption_count: int
    enabled: bool
    applicable_ticket_type_ids: list[str] | None
    visit_count: int
    created_at: datetime
    updated_at: datetime
    state: str
    held_redemptions: int
    campaign_url: str


class CampaignResolution(BaseModel):
    event_id: UUID
    event_title: str
    campaign_name: str
    state: str
    # Present only when the code can currently be applied; checkout re-validates it anyway.
    promo_code: str | None
    discount_type: str
    discount_value: int
    message: str | None


COMPUTED_FIELDS = {"state", "held_redemptions", "campaign_url"}


def to_read(db: Session, campaign: Campaign) -> CampaignRead:
    held = held_redemptions(db, campaign)
    stored = {
        key: getattr(campaign, key) for key in CampaignRead.model_fields.keys() - COMPUTED_FIELDS
    }
    return CampaignRead(
        **stored,
        state=campaign_state(campaign, datetime.now(UTC), held),
        held_redemptions=held,
        campaign_url=campaign_link(campaign),
    )


def checked_ticket_types(db: Session, event: Event, ids: list[UUID] | None) -> list[str] | None:
    if not ids:
        return None
    found = set(
        db.scalars(
            select(TicketType.id).where(TicketType.event_id == event.id, TicketType.id.in_(ids))
        ).all()
    )
    if found != set(ids):
        raise HTTPException(422, detail="Every applicable ticket type must belong to this event")
    return [str(ticket_type_id) for ticket_type_id in ids]


def managed_campaign(db: Session, event_id: UUID, campaign_id: UUID, user: User):
    event = require_event_manager(db, event_id, user)
    campaign = db.get(Campaign, campaign_id)
    if not campaign or campaign.event_id != event.id:
        raise HTTPException(404, detail="Campaign not found")
    return event, campaign


@router.post("/events/{event_id}/campaigns", response_model=CampaignRead, status_code=201)
def create_campaign(
    event_id: UUID,
    data: CampaignCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    event = require_event_manager(db, event_id, user)
    if event.status == "cancelled":
        raise HTTPException(409, detail="Cannot create campaigns for a cancelled event")
    if db.scalar(select(Campaign.id).where(Campaign.code == data.code)):
        raise HTTPException(409, detail=f"Promo code {data.code} is already in use")
    campaign = Campaign(
        event_id=event.id,
        opaque_token=secrets.token_urlsafe(24),
        **data.model_dump(exclude={"applicable_ticket_type_ids"}),
        applicable_ticket_type_ids=checked_ticket_types(db, event, data.applicable_ticket_type_ids),
    )
    db.add(campaign)
    db.flush()
    audit(
        db,
        user,
        event,
        "campaign.created",
        "campaign",
        campaign.id,
        f"Promo campaign {campaign.name} ({campaign.code}) created",
    )
    db.commit()
    db.refresh(campaign)
    return to_read(db, campaign)


@router.get("/events/{event_id}/campaigns", response_model=list[CampaignRead])
def list_campaigns(
    event_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    require_event_manager(db, event_id, user)
    campaigns = db.scalars(
        select(Campaign).where(Campaign.event_id == event_id).order_by(Campaign.created_at.desc())
    ).all()
    return [to_read(db, campaign) for campaign in campaigns]


@router.get("/events/{event_id}/campaigns/{campaign_id}", response_model=CampaignRead)
def read_campaign(
    event_id: UUID,
    campaign_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    return to_read(db, managed_campaign(db, event_id, campaign_id, user)[1])


@router.patch("/events/{event_id}/campaigns/{campaign_id}", response_model=CampaignRead)
def update_campaign(
    event_id: UUID,
    campaign_id: UUID,
    data: CampaignUpdate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    event, campaign = managed_campaign(db, event_id, campaign_id, user)
    changes = data.model_dump(exclude_unset=True)
    if "applicable_ticket_type_ids" in changes:
        changes["applicable_ticket_type_ids"] = checked_ticket_types(
            db, event, data.applicable_ticket_type_ids
        )
    valid_from = changes.get("valid_from", campaign.valid_from)
    valid_until = changes.get("valid_until", campaign.valid_until)
    if utc(valid_until) <= utc(valid_from):
        raise HTTPException(422, detail="valid_until must be after valid_from")
    minimum = campaign.redemption_count + held_redemptions(db, campaign)
    if changes.get("max_redemptions", minimum) < minimum:
        raise HTTPException(
            422, detail=f"Maximum redemptions cannot be lower than the {minimum} already used"
        )
    was_enabled = campaign.enabled
    for key, value in changes.items():
        setattr(campaign, key, value)
    if was_enabled != campaign.enabled:
        action = "campaign.enabled" if campaign.enabled else "campaign.disabled"
        description = f"Promo code {campaign.code} {'enabled' if campaign.enabled else 'disabled'}"
    else:
        action, description = "campaign.updated", f"Promo campaign {campaign.code} updated"
    audit(db, user, event, action, "campaign", campaign.id, description)
    db.commit()
    db.refresh(campaign)
    return to_read(db, campaign)


@router.get("/events/{event_id}/campaigns/{campaign_id}/qr")
def campaign_qr(
    event_id: UUID,
    campaign_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _, campaign = managed_campaign(db, event_id, campaign_id, user)
    return Response(
        campaign_qr_png(campaign),
        media_type="image/png",
        headers={
            "Content-Disposition": f'inline; filename="campaign-{campaign.code}.png"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/campaigns/resolve/{token}", response_model=CampaignResolution)
def resolve_campaign(token: str, db: Session = Depends(get_db)):
    """Public landing lookup for a scanned Campaign QR; counts the visit."""
    campaign = db.scalar(select(Campaign).where(Campaign.opaque_token == token))
    event = db.get(Event, campaign.event_id) if campaign else None
    if not campaign or not event or event.status == "draft" or event.visibility == "private":
        raise HTTPException(404, detail="This promotional link is not valid")
    db.execute(
        update(Campaign)
        .where(Campaign.id == campaign.id)
        .values(visit_count=Campaign.visit_count + 1)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    state = (
        "event_cancelled"
        if event.status == "cancelled"
        else campaign_state(campaign, datetime.now(UTC), held_redemptions(db, campaign))
    )
    return CampaignResolution(
        event_id=event.id,
        event_title=event.title,
        campaign_name=campaign.name,
        state=state,
        promo_code=campaign.code if state == "active" else None,
        discount_type=campaign.discount_type,
        discount_value=campaign.discount_value,
        message="This event has been cancelled."
        if state == "event_cancelled"
        else state_message(campaign, state),
    )
