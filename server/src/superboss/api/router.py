from fastapi import APIRouter

from superboss.modules.auth.router import router as auth_router
from superboss.modules.devices.router import router as devices_router
from superboss.modules.files.router import router as files_router
from superboss.modules.health.router import router as health_router
from superboss.modules.imports.router import router as imports_router
from superboss.modules.projects.router import router as projects_router
from superboss.modules.users.router import router as users_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(devices_router)
api_router.include_router(projects_router)
api_router.include_router(files_router)
api_router.include_router(imports_router)
api_router.include_router(users_router)
