import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import models  # noqa: F401 - registers ORM metadata before startup
from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.database import create_schema


@asynccontextmanager
async def lifespan(application):
    async def cleanup():
        from app.orders.reservations import sweep

        while True:
            try:
                await asyncio.to_thread(sweep)
            except Exception:
                logging.getLogger(__name__).exception("Reservation cleanup failed; retrying")
            await asyncio.sleep(30)

    task = asyncio.create_task(cleanup())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def create_app() -> FastAPI:
    settings = get_settings()
    create_schema()
    application = FastAPI(
        title=settings.app_name,
        debug=settings.app_debug,
        version="0.1.0",
        lifespan=lifespan,
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
