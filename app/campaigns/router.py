import io
import secrets
from datetime import datetime
from uuid import UUID

import qrcode
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.campaigns.models import Campaign
from app.core.config import get_settings
from app.core.database import get_db
from app.core.dependencies import current_user
from app.events.service import audit, require_event_manager
from app.users.models import User

router = APIRouter(tags=["campaigns"])


class CampaignCreate(BaseModel):
    name: str
    code: str = Field(min_length=3, max_length=40)
    discount_type: str = Field(pattern="^(percentage|fixed_kzt)$")
    discount_value: int = Field(gt=0)
    valid_from: datetime
    valid_until: datetime
    max_redemptions: int = Field(gt=0)


@router.post("/events/{event_id}/campaigns", status_code=201)
def create_campaign(
    event_id: UUID,
    data: CampaignCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    event = require_event_manager(db, event_id, user)
    if data.valid_until <= data.valid_from or (
        data.discount_type == "percentage" and data.discount_value > 100
    ):
        raise HTTPException(422, detail="Invalid campaign values")
    campaign = Campaign(
        event_id=event.id,
        opaque_token=secrets.token_urlsafe(24),
        **data.model_dump(exclude={"code"}),
        code=data.code.upper(),
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
        f"Campaign {campaign.name} created",
    )
    db.commit()
    db.refresh(campaign)
    return campaign


@router.get("/events/{event_id}/campaigns")
def list_campaigns(
    event_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    require_event_manager(db, event_id, user)
    return list(db.scalars(select(Campaign).where(Campaign.event_id == event_id)).all())


@router.get("/campaigns/resolve/{token}")
def resolve_campaign(token: str, db: Session = Depends(get_db)):
    campaign = db.scalar(select(Campaign).where(Campaign.opaque_token == token))
    if not campaign or not campaign.enabled:
        raise HTTPException(404, detail="Campaign not found")
    return {"event_id": str(campaign.event_id), "promo_code": campaign.code}


@router.get("/events/{event_id}/campaigns/{campaign_id}/qr")
def campaign_qr(
    event_id: UUID,
    campaign_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    require_event_manager(db, event_id, user)
    campaign = db.get(Campaign, campaign_id)
    if not campaign or campaign.event_id != event_id:
        raise HTTPException(404, detail="Campaign not found")
    image = qrcode.make(f"{get_settings().app_frontend_url}/campaign/{campaign.opaque_token}")
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    stream.seek(0)
    return StreamingResponse(
        stream,
        media_type="image/png",
        headers={"Content-Disposition": f'inline; filename="campaign-{campaign.id}.png"'},
    )
