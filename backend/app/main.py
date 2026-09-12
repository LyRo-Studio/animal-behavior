from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.accounts import router as accounts_router
from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.core.config import settings
from app.db.session import SessionLocal
from app.services.accounts import seed_first_admin


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Idempotent (see seed_first_admin) — safe to run on every startup.
    db = SessionLocal()
    try:
        seed_first_admin(db)
    finally:
        db.close()

    yield


app = FastAPI(title="Animal Behavior API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(accounts_router)
app.include_router(admin_router)
