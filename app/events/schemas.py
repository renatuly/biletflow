from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class VenueWrite(BaseModel):
    name: str = Field(min_length=2, max_length=250)
    address: str = Field(min_length=3, max_length=500)
    city: str = "Almaty"


class EventCreate(BaseModel):
    title: str = Field(min_length=3, max_length=250)
    description: str = Field(min_length=3)
    category: str = Field(min_length=2, max_length=100)
    visibility: str = Field(default="public", pattern="^(public|unlisted|private)$")
    venue: VenueWrite
    starts_at: datetime
    ends_at: datetime
    time_zone: str = "Asia/Almaty"
    registration_opens_at: datetime
    registration_closes_at: datetime
    capacity: int = Field(gt=0)
    admission_type: str = Field(
        default="general_admission", pattern="^(general_admission|assigned_seating)$"
    )
    refund_policy: str = "No refunds"

    @model_validator(mode="after")
    def validate_dates(self):
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        if self.registration_closes_at > self.starts_at:
            raise ValueError("registration must close no later than the event start")
        if self.registration_opens_at >= self.registration_closes_at:
            raise ValueError("registration opening must precede closing")
        return self


class EventUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=250)
    description: str | None = Field(default=None, min_length=3)
    category: str | None = None
    visibility: str | None = Field(default=None, pattern="^(public|unlisted|private)$")
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    registration_opens_at: datetime | None = None
    registration_closes_at: datetime | None = None
    capacity: int | None = Field(default=None, gt=0)
    refund_policy: str | None = None


class EventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    organizer_id: UUID
    venue_id: UUID
    title: str
    description: str
    category: str
    images: list
    visibility: str
    status: str
    starts_at: datetime
    ends_at: datetime
    time_zone: str
    registration_opens_at: datetime
    registration_closes_at: datetime
    capacity: int
    admission_type: str
    refund_policy: str
    paid_sales_status: str
    created_at: datetime


class OrganizerProfileWrite(BaseModel):
    contact_name: str
    organization_name: str | None = None
    contact_phone: str | None = None


class OrganizerProfileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    user_id: UUID
    contact_name: str
    organization_name: str | None
    contact_phone: str | None
    verification_status: str
    payout_account_masked: str | None
    payout_verified: bool
