from fastapi import APIRouter

from superboss.modules.auth.router import router as auth_router
from superboss.modules.health.router import router as health_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(auth_router)
