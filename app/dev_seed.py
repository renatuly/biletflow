"""Create deterministic local data for exercising every mobile-app role."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app import models  # noqa: F401 - registers all ORM metadata
from app.core.config import get_settings
from app.core.database import SessionLocal, create_schema
from app.core.security import create_qr_credential, hash_password
from app.events.models import Event, StaffAssignment, Venue
from app.orders.models import Order, OrderItem, Payment
from app.tickets.models import CheckInRecord, Ticket, TicketStatus, TicketType
from app.users.models import User

DEMO_PASSWORD = "DemoPass123!"
CORE_USERS = (
    ("attendee.demo@example.com", "Demo Attendee", ["attendee"]),
    ("organizer.demo@example.com", "Demo Organizer", ["attendee", "organizer"]),
    ("staff.demo@example.com", "Demo Check-in Staff", ["attendee", "event_admin"]),
    ("admin.demo@example.com", "Demo Administrator", ["attendee", "platform_admin"]),
    ("city.organizer@example.com", "City Events KZ", ["attendee", "organizer"]),
)
GUEST_NAMES = (
    "Aruzhan Sarsenova",
    "Dias Nurgaliyev",
    "Aigerim Tolegen",
    "Miras Bekov",
    "Dana Seitova",
    "Alikhan Omarov",
    "Madina Iskakova",
    "Timur Zhaksylykov",
    "Aidana Serik",
    "Sanzhar Alimov",
    "Kamila Rakhimova",
    "Nursultan Imanov",
    "Zarina Akhmetova",
    "Adil Mukhanov",
    "Tomiris Asanova",
    "Erlan Kenzhebek",
    "Aliya Baimurat",
    "Bekzat Ospanov",
    "Sabina Yessim",
    "Daniyar Kaliyev",
)


@dataclass(frozen=True)
class EventSeed:
    title: str
    description: str
    category: str
    venue_name: str
    address: str
    city: str
    days_from_now: int
    duration_hours: int
    price_minor: int
    capacity: int
    registrations: int
    organizer_email: str = "organizer.demo@example.com"
    status: str = "published"
    demo_ticket: bool = False


EVENTS = (
    EventSeed(
        "Almaty Jazz Night",
        "An evening of live jazz with leading artists from across Kazakhstan.",
        "music",
        "Republic Palace",
        "56 Dostyk Avenue",
        "Almaty",
        7,
        3,
        1_200_000,
        240,
        12,
        demo_ticket=True,
    ),
    EventSeed(
        "Astana Football Derby",
        "A high-energy evening match featuring two of the capital's top clubs.",
        "sport",
        "Astana Arena",
        "48 Turan Avenue",
        "Astana",
        10,
        3,
        900_000,
        300,
        14,
        organizer_email="city.organizer@example.com",
        demo_ticket=True,
    ),
    EventSeed(
        "Digital Nomads Summit",
        "Product leaders, founders, and engineers discuss the future of technology.",
        "business",
        "Atakent Congress Hall",
        "42 Timiryazev Street",
        "Almaty",
        12,
        8,
        4_500_000,
        280,
        18,
    ),
    EventSeed(
        "Modern Art Weekend",
        "A free weekend exhibition of contemporary painting, sculpture, and media art.",
        "art",
        "Kasteyev Museum",
        "30A Satpayev Street",
        "Almaty",
        18,
        7,
        0,
        200,
        8,
        demo_ticket=True,
    ),
    EventSeed(
        "Taste of Kazakhstan",
        "Regional chefs present traditional dishes with a modern interpretation.",
        "food",
        "First President Park",
        "Dulati Avenue",
        "Almaty",
        24,
        6,
        800_000,
        260,
        15,
    ),
    EventSeed(
        "Kazakh Design Expo",
        "Fashion, architecture, and product design from emerging Kazakh creators.",
        "art",
        "EXPO Congress Centre",
        "53/1 Mangilik El Avenue",
        "Astana",
        28,
        7,
        500_000,
        180,
        9,
        organizer_email="city.organizer@example.com",
    ),
    EventSeed(
        "BiletFlow Demo Event",
        "A free community event for testing registration and mobile tickets.",
        "music",
        "BiletFlow Demo Hall",
        "100 Abay Avenue",
        "Almaty",
        30,
        3,
        0,
        160,
        8,
        demo_ticket=True,
    ),
    EventSeed(
        "Almaty City Run",
        "A city running festival with 5 km, 10 km, and half-marathon routes.",
        "sport",
        "Central Stadium",
        "48 Abay Avenue",
        "Almaty",
        35,
        5,
        600_000,
        400,
        20,
    ),
    EventSeed(
        "Street Food Festival",
        "Food trucks, local coffee roasters, and live entertainment for all ages.",
        "food",
        "Botanical Garden",
        "71 Al-Farabi Avenue",
        "Almaty",
        42,
        7,
        0,
        300,
        11,
        organizer_email="city.organizer@example.com",
    ),
    EventSeed(
        "Indie Music Open Air",
        "An outdoor showcase of independent bands and electronic performers.",
        "music",
        "Oi-Qaragai Amphitheatre",
        "Besqaynar Village",
        "Almaty",
        45,
        6,
        1_500_000,
        220,
        10,
    ),
    EventSeed(
        "Startup Pitch Night",
        "Early-stage teams pitch new products to founders, mentors, and investors.",
        "business",
        "MOST Hub",
        "280 Baizakov Street",
        "Almaty",
        55,
        4,
        0,
        140,
        6,
    ),
    EventSeed(
        "Spring Rock Festival",
        "A completed festival retained to demonstrate ticket history and check-in data.",
        "music",
        "Almaty Arena",
        "7 Momyshuly Street",
        "Almaty",
        -20,
        5,
        1_000_000,
        240,
        5,
        status="completed",
        demo_ticket=True,
    ),
    EventSeed(
        "Secret Rooftop Session",
        "A draft event visible to the organizer while programming is finalized.",
        "music",
        "Rooftop 28",
        "28 Zheltoksan Street",
        "Almaty",
        60,
        3,
        2_000_000,
        100,
        0,
        status="draft",
    ),
    EventSeed(
        "Cancelled Demo Conference",
        "A cancelled listing used to demonstrate event lifecycle states.",
        "business",
        "Forum Centre",
        "12 Saryarka Avenue",
        "Astana",
        70,
        6,
        3_500_000,
        180,
        0,
        status="cancelled",
    ),
)


def upsert_user(db, email: str, full_name: str, roles: list[str], password_hash: str) -> User:
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email, full_name=full_name)
        db.add(user)
    user.full_name = full_name
    user.password_hash = password_hash
    user.roles = roles
    user.email_verified = True
    user.is_active = True
    db.flush()
    return user


def upsert_ticket_type(
    db,
    event: Event,
    name: str,
    description: str,
    kind: str,
    price_minor: int,
    quantity: int,
    sales_start: datetime,
    sales_end: datetime,
    max_per_order: int,
) -> TicketType:
    ticket_type = db.scalar(
        select(TicketType).where(TicketType.event_id == event.id, TicketType.name == name)
    )
    if ticket_type is None:
        ticket_type = TicketType(event_id=event.id, name=name)
        db.add(ticket_type)
    ticket_type.description = description
    ticket_type.kind = kind
    ticket_type.price_minor = price_minor
    sold_and_reserved = (ticket_type.sold_count or 0) + (ticket_type.reserved_count or 0)
    ticket_type.quantity = max(quantity, sold_and_reserved)
    ticket_type.sales_start = sales_start
    ticket_type.sales_end = sales_end
    ticket_type.max_per_order = max_per_order
    ticket_type.is_hidden = False
    db.flush()
    return ticket_type


def upsert_event(
    db, spec: EventSeed, users: dict[str, User], now: datetime
) -> tuple[Event, TicketType]:
    organizer = users[spec.organizer_email]
    starts_at = now + timedelta(days=spec.days_from_now)
    event = db.scalar(
        select(Event).where(Event.organizer_id == organizer.id, Event.title == spec.title)
    )
    if event is None:
        venue = Venue(name=spec.venue_name, address=spec.address, city=spec.city)
        db.add(venue)
        db.flush()
        event = Event(
            organizer_id=organizer.id,
            venue_id=venue.id,
            title=spec.title,
            description=spec.description,
            category=spec.category,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=spec.duration_hours),
            registration_opens_at=starts_at - timedelta(days=60),
            registration_closes_at=starts_at,
            capacity=spec.capacity,
        )
        db.add(event)
        db.flush()
    venue = db.get(Venue, event.venue_id)
    venue.name = spec.venue_name
    venue.address = spec.address
    venue.city = spec.city
    event.description = spec.description
    event.category = spec.category
    event.visibility = "public"
    event.status = spec.status
    event.starts_at = starts_at
    event.ends_at = starts_at + timedelta(hours=spec.duration_hours)
    event.registration_opens_at = starts_at - timedelta(days=60)
    event.registration_closes_at = starts_at
    event.capacity = spec.capacity
    event.paid_sales_status = (
        "suspended"
        if spec.status == "cancelled"
        else "active"
        if spec.price_minor > 0 and spec.status == "published"
        else "not_ready"
    )
    event.activation_fee_paid = spec.price_minor > 0 and spec.status == "published"
    event.paid_terms_accepted_at = now if event.activation_fee_paid else None
    sales_start = starts_at - timedelta(days=60)
    general_capacity = spec.capacity if spec.price_minor == 0 else max(1, spec.capacity - 40)
    general = upsert_ticket_type(
        db,
        event,
        "General admission",
        "Standard entry to the event",
        "free" if spec.price_minor == 0 else "paid",
        spec.price_minor,
        general_capacity,
        sales_start,
        starts_at,
        6,
    )
    if spec.price_minor > 0:
        upsert_ticket_type(
            db,
            event,
            "VIP",
            "Priority entry and reserved lounge access",
            "paid",
            spec.price_minor * 2,
            40,
            sales_start,
            starts_at,
            4,
        )
    return event, general


def ensure_staff_assignment(db, event: Event, staff: User) -> None:
    assignment = db.scalar(
        select(StaffAssignment).where(
            StaffAssignment.event_id == event.id,
            StaffAssignment.user_id == staff.id,
        )
    )
    if assignment is None:
        db.add(
            StaffAssignment(
                event_id=event.id,
                user_id=staff.id,
                permissions=["check_in", "reverse_check_in", "manage_attendees"],
            )
        )


def ensure_registration(
    db,
    event: Event,
    ticket_type: TicketType,
    attendee: User,
    staff: User,
    now: datetime,
    processing_fee_bps: int,
) -> bool:
    existing = db.scalar(
        select(Order).where(
            Order.event_id == event.id,
            Order.user_id == attendee.id,
            Order.status == "confirmed",
        )
    )
    if existing is not None:
        return False
    subtotal = ticket_type.price_minor
    fee = subtotal * processing_fee_bps // 10_000
    order = Order(
        event_id=event.id,
        user_id=attendee.id,
        attendee_name=attendee.full_name,
        attendee_email=attendee.email,
        status="confirmed",
        subtotal_minor=subtotal,
        processing_fee_minor=fee,
        total_minor=subtotal + fee,
        expires_at=now + timedelta(minutes=15),
    )
    db.add(order)
    db.flush()
    item = OrderItem(
        order_id=order.id,
        ticket_type_id=ticket_type.id,
        quantity=1,
        unit_price_minor=ticket_type.price_minor,
    )
    db.add(item)
    db.flush()
    db.add(
        Payment(
            order_id=order.id,
            provider_reference=f"seed_{order.id.hex}",
            status="succeeded",
            amount_minor=order.total_minor,
        )
    )
    ticket_status = TicketStatus.CHECKED_IN if event.status == "completed" else TicketStatus.VALID
    ticket = Ticket(
        order_item_id=item.id,
        event_id=event.id,
        ticket_type_id=ticket_type.id,
        owner_id=attendee.id,
        attendee_name=attendee.full_name,
        attendee_email=attendee.email,
        status=ticket_status,
        qr_credential="pending",
    )
    db.add(ticket)
    db.flush()
    ticket.qr_credential = create_qr_credential(str(ticket.id))
    ticket_type.sold_count += 1
    if ticket_status == TicketStatus.CHECKED_IN:
        db.add(
            CheckInRecord(
                ticket_id=ticket.id,
                event_id=event.id,
                staff_user_id=staff.id,
                device_id="demo-seed",
            )
        )
    return True


def seed() -> None:
    settings = get_settings()
    if settings.app_env == "production":
        raise RuntimeError("The demo seed is disabled when APP_ENV=production")

    create_schema()
    now = datetime.now(UTC)
    password_hash = hash_password(DEMO_PASSWORD)
    guest_users = tuple(
        (f"guest{index:02d}.demo@example.com", name, ["attendee"])
        for index, name in enumerate(GUEST_NAMES, start=1)
    )
    all_users = (*CORE_USERS, *guest_users)
    with SessionLocal() as db:
        users = {
            email: upsert_user(db, email, full_name, roles, password_hash)
            for email, full_name, roles in all_users
        }
        demo_attendee = users["attendee.demo@example.com"]
        staff = users["staff.demo@example.com"]
        guests = [users[email] for email, _, _ in guest_users]
        created_registrations = 0
        for spec in EVENTS:
            event, general = upsert_event(db, spec, users, now)
            if spec.organizer_email == "organizer.demo@example.com":
                ensure_staff_assignment(db, event, staff)
            if spec.registrations == 0:
                continue
            registrants = guests[: spec.registrations]
            if spec.demo_ticket:
                registrants = [demo_attendee, *guests[: spec.registrations - 1]]
            for attendee in registrants:
                created_registrations += ensure_registration(
                    db,
                    event,
                    general,
                    attendee,
                    staff,
                    now,
                    settings.app_processing_fee_bps,
                )
        db.commit()
        totals = {
            "users": db.scalar(select(func.count(User.id))) or 0,
            "events": db.scalar(select(func.count(Event.id))) or 0,
            "orders": db.scalar(select(func.count(Order.id))) or 0,
            "tickets": db.scalar(select(func.count(Ticket.id))) or 0,
        }

    print(
        "Development demo data is ready: "
        f"{totals['users']} users, {totals['events']} events, "
        f"{totals['orders']} orders, and {totals['tickets']} tickets "
        f"({created_registrations} registrations added this run)."
    )
    print("Primary demo accounts:")
    for email, _, _ in CORE_USERS[:4]:
        print(f"  {email} / {DEMO_PASSWORD}")


if __name__ == "__main__":
    seed()
