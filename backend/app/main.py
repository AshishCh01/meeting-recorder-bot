import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.api import chat, meetings, webhooks
from app.db.database import SessionLocal
from app.services.watchdog import sweep_stale_meetings

logger = logging.getLogger(__name__)


async def _watchdog_loop():
    interval_seconds = settings.watchdog_sweep_interval_minutes * 60
    while True:
        try:
            db = SessionLocal()
            try:
                # Run the sync SQLAlchemy sweep off the event loop thread,
                # same reasoning FastAPI already applies to sync route
                # handlers - this loop runs on the loop thread directly
                # since it's a plain asyncio.create_task, not a request.
                await asyncio.to_thread(sweep_stale_meetings, db)
            finally:
                db.close()
        except Exception:
            logger.exception("[watchdog] sweep failed")
        await asyncio.sleep(interval_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = None
    if settings.watchdog_enabled:
        task = asyncio.create_task(_watchdog_loop())
    yield
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="Meeting Recorder API", lifespan=lifespan)


cors_origins = [settings.frontend_origin]
if settings.environment == "development":
    cors_origins += ["http://localhost:5173", "http://127.0.0.1:5173"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,       # <-- Added this for auth compatibility
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(meetings.router)
app.include_router(webhooks.router)
app.include_router(chat.router)


@app.get("/health")
def health():
    return {"status": "ok"}
