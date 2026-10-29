from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class CheckoutItem(BaseModel):
    ticket_type_id: UUID
    quantity: int = Field(gt=0, le=50)


class CheckoutCreate(BaseModel):
    event_id: UUID
    attendee_name: str = Field(min_length=2, max_length=200)
    attendee_email: EmailStr
    items: list[CheckoutItem] = Field(min_length=1)
    promo_code: str | None = Field(default=None, max_length=40)


class OrderItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    ticket_type_id: UUID
    quantity: int
    unit_price_minor: int
    discount_minor: int


class OrderRead(BaseModel):
    can_cancel_registration: bool = False
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    event_id: UUID
    user_id: UUID | None
    attendee_name: str
    attendee_email: str
    status: str
    currency: str
    subtotal_minor: int
    discount_minor: int
    processing_fee_minor: int
    total_minor: int
    expires_at: datetime
    campaign_id: UUID | None
    promo_code: str | None = None
    items: list[OrderItemRead] = []
    created_at: datetime


class QuoteRequest(BaseModel):
    event_id: UUID
    items: list[CheckoutItem] = Field(min_length=1)
    promo_code: str | None = Field(default=None, max_length=40)


class QuoteRead(BaseModel):
    subtotal_minor: int
    discount_minor: int
    processing_fee_minor: int
    total_minor: int
    currency: str = "KZT"
    promo_code: str | None
    campaign_name: str | None = None
    is_simulation: bool = True


class PaymentSimulation(BaseModel):
    outcome: str = Field(default="success", pattern="^(success|failure)$")


class RefundRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)
