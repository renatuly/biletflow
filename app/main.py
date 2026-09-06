from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import models  # noqa: F401 - registers ORM metadata before startup
from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.database import create_schema


def create_app() -> FastAPI:
    settings = get_settings()
    create_schema()
    application = FastAPI(
        title=settings.app_name,
        debug=settings.app_debug,
        version="0.1.0",
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.app_frontend_url],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(api_router, prefix=settings.app_api_v1_prefix)
    return application


app = create_app()
