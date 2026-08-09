from fastapi import FastAPI

from superboss.api.router import api_router
from superboss.core.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    active_settings = settings or get_settings()
    app = FastAPI(title="SuperBoss API", version="1.0.0")
    app.state.settings = active_settings
    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()
