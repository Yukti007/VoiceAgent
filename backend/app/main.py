"""FastAPI app: token minting + call/appointment/extraction read endpoints for
the demo frontend. The realtime voice pipeline itself runs in a separate
process (app/agent/agent.py), started independently as a LiveKit worker.

Run with:
    uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import ensure_dirs, get_settings
from app.database.database import init_db

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Voice Agent V0 API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.on_event("startup")
def on_startup() -> None:
    ensure_dirs()
    init_db()
    missing = settings.missing_credentials()
    if missing:
        logger.warning(
            "Missing/placeholder credentials: %s. Add real values to backend/.env "
            "before starting a realtime call.",
            ", ".join(missing),
        )
    else:
        logger.info("All required credentials are configured.")


@app.get("/")
def root() -> dict:
    return {"name": "Voice Agent V0 API", "docs": "/docs"}
