from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TicketTypeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    description: str = ""
    kind: str = Field(pattern="^(free|paid)$")
    price_minor: int = Field(ge=0)
    quantity: int = Field(gt=0)
    sales_start: datetime
    sales_end: datetime
    max_per_order: int = Field(default=10, gt=0, le=50)

    @model_validator(mode="after")
    def validate_ticket_type(self):
        if self.sales_end <= self.sales_start:
            raise ValueError("sales_end must follow sales_start")
        if self.kind == "free" and self.price_minor != 0:
            raise ValueError("free tickets must have zero price")
        if self.kind == "paid" and self.price_minor <= 0:
            raise ValueError("paid tickets require a positive price")
        return self


class TicketTypeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    event_id: UUID
    name: str
    description: str
    kind: str
    price_minor: int
    currency: str
    quantity: int
    reserved_count: int
    sold_count: int
    refunded_count: int
    sales_start: datetime
    sales_end: datetime
    max_per_order: int
    is_hidden: bool


class TicketRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    event_id: UUID
    ticket_type_id: UUID
    attendee_name: str
    attendee_email: str
    status: str
    section: str | None
    row: str | None
    seat: str | None
