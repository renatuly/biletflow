"""Import all ORM models so SQLAlchemy can resolve foreign keys."""

from app.campaigns.models import Campaign, PromoRedemption
from app.events.models import AuditLog, Event, OrganizerProfile, StaffAssignment, Venue
from app.events.paid_applications import PaidEventApplication
from app.orders.models import Order, OrderItem, Payment, Refund
from app.platform.operations_models import (
    ActivationFeeRecord,
    CheckoutAttempt,
    EventInvitation,
    EventReport,
    OrderDispute,
    PlatformSettings,
    SimulatedPayout,
)
from app.support.models import SupportCase, SupportChange, SupportMessage
from app.tickets.models import CheckInRecord, Ticket, TicketType
from app.users.models import Notification, OrganizerApplication, User

__all__ = [
    "CheckoutAttempt",
    "ActivationFeeRecord",
    "EventInvitation",
    "EventReport",
    "OrderDispute",
    "PlatformSettings",
    "SimulatedPayout",
    "AuditLog",
    "Campaign",
    "CheckInRecord",
    "Event",
    "Notification",
    "Order",
    "OrderItem",
    "OrganizerProfile",
    "OrganizerApplication",
    "Payment",
    "PaidEventApplication",
    "Refund",
    "StaffAssignment",
    "Ticket",
    "PromoRedemption",
    "SupportCase",
    "SupportChange",
    "SupportMessage",
    "TicketType",
    "User",
    "Venue",
]
