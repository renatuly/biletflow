from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import current_user, require_role
from app.events.models import AuditLog, OrganizerProfile
from app.users.models import Notification, OrganizerApplication, User, UserRole

router = APIRouter(tags=["organizer applications"])


class ApplicationCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    contact_name: str = Field(min_length=2, max_length=200)
    organization_name: str = Field(min_length=2, max_length=200)
    contact_phone: str = Field(min_length=5, max_length=40)
    description: str = Field(min_length=10, max_length=2000)


class ApplicationRead(ApplicationCreate):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    user_id: UUID
    status: str
    created_at: datetime
    updated_at: datetime


class AdminApplicationRead(ApplicationRead):
    email: str | None = None
    applicant_name: str | None = None


class RejectionWrite(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)


@router.get("/organizer-applications/me", response_model=ApplicationRead | None)
def my_application(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return db.scalar(select(OrganizerApplication).where(OrganizerApplication.user_id == user.id))


@router.post("/organizer-applications", response_model=ApplicationRead, status_code=201)
def submit_application(
    data: ApplicationCreate, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    if UserRole.ORGANIZER in user.roles or UserRole.PLATFORM_ADMIN in user.roles:
        raise HTTPException(409, detail="This account already has organizer access")
    existing = db.scalar(
        select(OrganizerApplication).where(OrganizerApplication.user_id == user.id)
    )
    if existing and existing.status != "rejected":
        raise HTTPException(409, detail="An organizer application already exists for this account")
    if existing:
        # A rejected applicant may correct their details and apply again (one row per user).
        for key, value in data.model_dump().items():
            setattr(existing, key, value)
        existing.status = "pending"
        application = existing
    else:
        application = OrganizerApplication(user_id=user.id, **data.model_dump())
        db.add(application)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(409, detail="An organizer application already exists") from error
    db.refresh(application)
    return application


@router.get("/admin/organizer-applications", response_model=list[AdminApplicationRead])
def pending_applications(
    admin: User = Depends(require_role(UserRole.PLATFORM_ADMIN)),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(OrganizerApplication, User)
        .join(User, User.id == OrganizerApplication.user_id)
        .where(OrganizerApplication.status == "pending")
        .order_by(OrganizerApplication.created_at)
    ).all()
    return [
        AdminApplicationRead.model_validate(application).model_copy(
            update={"email": applicant.email, "applicant_name": applicant.full_name}
        )
        for application, applicant in rows
    ]


@router.post(
    "/admin/organizer-applications/{application_id}/approve", response_model=ApplicationRead
)
def approve_application(
    application_id: UUID,
    admin: User = Depends(require_role(UserRole.PLATFORM_ADMIN)),
    db: Session = Depends(get_db),
):
    application = db.scalar(
        select(OrganizerApplication)
        .where(OrganizerApplication.id == application_id)
        .with_for_update()
    )
    if not application:
        raise HTTPException(404, detail="Organizer application not found")
    if application.status == "approved":
        return application
    if application.status != "pending":
        raise HTTPException(409, detail="Only pending applications can be approved")
    applicant = db.scalar(select(User).where(User.id == application.user_id).with_for_update())
    if not applicant or not applicant.is_active:
        raise HTTPException(409, detail="Applicant account is unavailable or inactive")
    if UserRole.ORGANIZER not in applicant.roles:
        applicant.roles = [*applicant.roles, UserRole.ORGANIZER]
    profile = db.scalar(select(OrganizerProfile).where(OrganizerProfile.user_id == applicant.id))
    if profile is None:
        db.add(
            OrganizerProfile(
                user_id=applicant.id,
                contact_name=application.contact_name,
                organization_name=application.organization_name,
                contact_phone=application.contact_phone,
            )
        )
    application.status = "approved"
    db.add(
        Notification(
            user_id=applicant.id,
            kind="organizer_application_approved",
            title="Organizer application approved",
            message=(
                "Your application was approved. "
                "Refresh your profile to open your organizer dashboard."
            ),
            data={"application_id": str(application.id)},
        )
    )
    db.add(
        AuditLog(
            actor_id=admin.id,
            event_id=None,
            action="organizer_application.approved",
            entity_type="organizer_application",
            entity_id=str(application.id),
            description="Granted organizer access after administrator approval",
        )
    )
    db.commit()
    db.refresh(application)
    return application


@router.post(
    "/admin/organizer-applications/{application_id}/reject", response_model=ApplicationRead
)
def reject_application(
    application_id: UUID,
    data: RejectionWrite,
    admin: User = Depends(require_role(UserRole.PLATFORM_ADMIN)),
    db: Session = Depends(get_db),
):
    application = db.scalar(
        select(OrganizerApplication)
        .where(OrganizerApplication.id == application_id)
        .with_for_update()
    )
    if not application:
        raise HTTPException(404, detail="Organizer application not found")
    if application.status == "rejected":
        return application
    if application.status != "pending":
        raise HTTPException(409, detail="Only pending applications can be rejected")
    reason = data.reason.strip()
    if len(reason) < 3:
        raise HTTPException(422, detail="A rejection reason is required")
    application.status = "rejected"
    # The reason is delivered to the applicant and kept in the audit log; the application
    # table has no reason column and the schema is created without migrations.
    db.add(
        Notification(
            user_id=application.user_id,
            kind="organizer_application_rejected",
            title="Organizer application rejected",
            message=reason,
            data={"application_id": str(application.id)},
        )
    )
    db.add(
        AuditLog(
            actor_id=admin.id,
            event_id=None,
            action="organizer_application.rejected",
            entity_type="organizer_application",
            entity_id=str(application.id),
            description=reason,
        )
    )
    db.commit()
    db.refresh(application)
    return application
