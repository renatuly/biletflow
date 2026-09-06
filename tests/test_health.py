import asyncio

from app.health.router import health_check
from app.main import app


def test_health_check() -> None:
    response = asyncio.run(health_check())

    assert response.model_dump() == {"status": "ok", "service": "biletflow-api"}


def test_openapi_is_available() -> None:
    schema = app.openapi()

    assert schema["info"]["title"] == "BiletFlow API"
    assert "/api/v1/health" in schema["paths"]
