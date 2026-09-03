from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Report whether the API process is running."""
    return HealthResponse(status="ok", service="biletflow-api")
