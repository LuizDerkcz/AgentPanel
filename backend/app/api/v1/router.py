from fastapi import APIRouter

from app.api.v1.endpoints.accounts import router as accounts_router
from app.api.v1.endpoints.auth import router as auth_router
from app.api.v1.endpoints.agents import router as agents_router
from app.api.v1.endpoints.bot import router as bot_router
from app.api.v1.endpoints.columns import router as columns_router
from app.api.v1.endpoints.dm import router as dm_router
from app.api.v1.endpoints.forum import router as forum_router
from app.api.v1.endpoints.health import router as health_router
from app.api.v1.endpoints.notifications import router as notifications_router
from app.api.v1.endpoints.predictions import router as predictions_router


api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(accounts_router)
api_router.include_router(columns_router)
api_router.include_router(forum_router)
api_router.include_router(agents_router)
api_router.include_router(bot_router)
api_router.include_router(notifications_router)
api_router.include_router(dm_router)
api_router.include_router(predictions_router)
