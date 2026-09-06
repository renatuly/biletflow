from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db

router = APIRouter()


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Report whether the API process is running."""
    return HealthResponse(status="ok", service="biletflow-api")


@router.get("/ready", response_model=HealthResponse)
def readiness_check(db: Session = Depends(get_db)) -> HealthResponse:
    db.execute(text("SELECT 1"))
    return HealthResponse(status="ok", service="biletflow-api")
