"""Add repeatable sample support conversations without resetting existing demo data.

Run `python -m app.dev_seed` first if the four demo accounts are missing.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app import models  # noqa: F401
from app.core.config import get_settings
from app.core.database import SessionLocal, create_schema
from app.events.models import Event, StaffAssignment
from app.orders.models import Order, OrderItem
from app.support.access import history, notify
from app.support.models import SupportCase, SupportMessage
from app.tickets.models import Ticket
from app.users.models import User


def seed():
    if get_settings().app_env == "production":
        raise RuntimeError("Support demo data is disabled in production")
    create_schema()
    added = 0
    with SessionLocal() as db:
        users = {}
        for role in ("attendee", "organizer", "staff", "admin"):
            user = db.scalar(select(User).where(User.email == f"{role}.demo@example.com"))
            if user is None:
                raise RuntimeError("Run python -m app.dev_seed first; demo accounts are missing")
            users[role] = user
        order = db.scalar(
            select(Order)
            .join(Event, Event.id == Order.event_id)
            .where(
                Order.user_id == users["attendee"].id,
                Event.organizer_id == users["organizer"].id,
                Event.status == "published",
                Order.status == "confirmed",
                Order.total_minor > 0,
            )
            .order_by(Event.starts_at.desc())
        )
        if order is None:
            raise RuntimeError("Demo attendee needs a confirmed paid order for the demo organizer")
        ticket = db.scalar(
            select(Ticket)
            .join(OrderItem, Ticket.order_item_id == OrderItem.id)
            .where(OrderItem.order_id == order.id)
        )
        specs = [
            (
                "refund-review",
                "attendee",
                "refund",
                "open",
                "organizer",
                "[Demo] Please review my refund request",
                [
                    (
                        "attendee",
                        "I can no longer attend. Could you review my refund "
                        "request under the event policy?",
                    )
                ],
            ),
            (
                "ticket-delivery",
                "attendee",
                "ticket_delivery",
                "in_progress",
                "staff",
                "[Demo] Ticket email has not arrived",
                [
                    ("attendee", "Payment succeeded but I cannot find the ticket email."),
                    (
                        "staff",
                        "I am checking your booking. You can also open My "
                        "Tickets to view your QR ticket.",
                    ),
                    (
                        "attendee",
                        "I found it in My Tickets. Please help me check the email delivery too.",
                    ),
                ],
            ),
            (
                "entry-question",
                "attendee",
                "event_information",
                "waiting_for_customer",
                "staff",
                "[Demo] Accessible entrance question",
                [
                    ("attendee", "Which entrance should I use for step-free access?"),
                    (
                        "staff",
                        "I can help. Will you need assistance from the entrance to your seat?",
                    ),
                ],
            ),
            (
                "resolved-ticket",
                "attendee",
                "technical",
                "resolved",
                "staff",
                "[Demo] Ticket download question resolved",
                [
                    ("attendee", "Where can I download my ticket?"),
                    ("staff", "Open your order or ticket details and select Download ticket."),
                    ("attendee", "That worked, thank you!"),
                ],
            ),
            (
                "organizer-platform",
                "organizer_platform",
                "payment",
                "in_progress",
                "admin",
                "[Demo] Organizer payment report question",
                [
                    (
                        "organizer",
                        "Can you explain how the demonstration payment totals are calculated?",
                    ),
                    (
                        "admin",
                        "These are simulated KZT transactions. I am reviewing "
                        "the order totals and processing fees with you.",
                    ),
                ],
            ),
            (
                "staff-platform",
                "organizer_platform",
                "technical",
                "waiting_for_customer",
                "admin",
                "[Demo] Staff camera permission help",
                [
                    ("staff", "The scanner cannot open my camera. Can platform support help?"),
                    (
                        "admin",
                        "Please check camera permission in your phone settings. "
                        "Which device and operating system are you using?",
                    ),
                ],
            ),
        ]
        now = datetime.now(UTC)
        for index, (key, flow, category, status, assignee, subject, messages) in enumerate(specs):
            client_key = f"demo-support-v1:{key}"
            requester = users[
                "staff"
                if key == "staff-platform"
                else "organizer"
                if flow == "organizer_platform"
                else "attendee"
            ]
            if db.scalar(
                select(SupportCase).where(
                    SupportCase.requester_id == requester.id, SupportCase.client_key == client_key
                )
            ):
                continue
            if flow == "attendee" and assignee == "staff":
                assignment = db.scalar(
                    select(StaffAssignment).where(
                        StaffAssignment.event_id == order.event_id,
                        StaffAssignment.user_id == users["staff"].id,
                    )
                )
                if assignment is None:
                    db.add(
                        StaffAssignment(
                            event_id=order.event_id,
                            user_id=users["staff"].id,
                            permissions=["support"],
                        )
                    )
                elif "support" not in assignment.permissions:
                    assignment.permissions = [*assignment.permissions, "support"]
            created = now - timedelta(hours=12 - index)
            case = SupportCase(
                requester_id=requester.id,
                flow=flow,
                client_key=client_key,
                event_id=order.event_id if flow == "attendee" else None,
                order_id=order.id if flow == "attendee" else None,
                ticket_id=ticket.id if ticket and flow == "attendee" else None,
                assigned_to=users[assignee].id,
                category=category,
                status=status,
                subject=subject,
                created_at=created,
                updated_at=created + timedelta(minutes=len(messages)),
            )
            db.add(case)
            db.flush()
            history(db, case, requester, "created", None, "open")
            history(
                db,
                case,
                users["organizer"] if flow == "attendee" else users["admin"],
                "assignment",
                None,
                users[assignee].id,
            )
            if status != "open":
                history(db, case, users[assignee], "status", "open", status)
            for offset, (sender, body) in enumerate(messages):
                db.add(
                    SupportMessage(
                        case_id=case.id,
                        sender_id=users[sender].id,
                        body=body,
                        created_at=created + timedelta(minutes=offset + 1),
                    )
                )
            db.flush()
            notify(db, case, requester, "support_assignment", "Demo support assignment", subject)
            if len(messages) > 1:
                notify(db, case, users[assignee], "support_message", "Demo support reply", subject)
            added += 1
        db.commit()
    print(f"Support demo conversations ready: {added} added; existing conversations preserved.")


if __name__ == "__main__":
    seed()
