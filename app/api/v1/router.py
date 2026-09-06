from fastapi import APIRouter

from app.campaigns.router import router as campaigns_router
from app.events.router import router as events_router
from app.health.router import router as health_router
from app.orders.router import router as orders_router
from app.platform.router import router as platform_router
from app.support.router import router as support_router
from app.tickets.router import router as tickets_router
from app.users.router import router as users_router

api_router = APIRouter()
api_router.include_router(health_router, tags=["health"])
api_router.include_router(users_router)
api_router.include_router(events_router)
api_router.include_router(orders_router)
api_router.include_router(tickets_router)
api_router.include_router(campaigns_router)
api_router.include_router(support_router)
api_router.include_router(platform_router)
