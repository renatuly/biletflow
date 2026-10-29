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
    if existing:
        raise HTTPException(409, detail="An organizer application already exists for this account")
    application = OrganizerApplication(user_id=user.id, **data.model_dump())
    db.add(application)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(409, detail="An organizer application already exists") from error
    db.refresh(application)
    return application


@router.get("/admin/organizer-applications", response_model=list[ApplicationRead])
def pending_applications(
    admin: User = Depends(require_role(UserRole.PLATFORM_ADMIN)),
    db: Session = Depends(get_db),
):
    return list(
        db.scalars(
            select(OrganizerApplication)
            .where(OrganizerApplication.status == "pending")
            .order_by(OrganizerApplication.created_at)
        ).all()
    )


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
