"""FastAPI application entry point."""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .api.routes import router as routes_router
from .api.zones import router as zones_router


def _cors_origins() -> list[str]:
    raw = os.getenv("DELEEZY_CORS_ORIGINS", "*")
    origins = [origin.strip() for origin in raw.split(",") if origin.strip()]
    return origins or ["*"]


def create_app() -> FastAPI:
    application = FastAPI(
        title="Deleezy Optimization API",
        version=__version__,
        description=(
            "Route optimization and delivery-zone breaking for Deleezy's "
            "on-demand package delivery platform."
        ),
        contact={"name": "Deleezy"},
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(routes_router, prefix="/api/v1")
    application.include_router(zones_router, prefix="/api/v1")

    @application.get("/", include_in_schema=False)
    def index() -> dict[str, str]:
        return {
            "service": "deleezy-optimizer",
            "version": __version__,
            "docs": "/docs",
            "health": "/health",
        }

    @application.get("/health", tags=["ops"])
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return application


app = create_app()
