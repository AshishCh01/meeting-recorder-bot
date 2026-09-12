import asyncio
import logging
from contextlib import asynccontextmanager, suppress

import psycopg
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError, TimeoutError as PoolTimeoutError
from app.config import settings
from app.api import calendar, chat, meetings, users, webhooks
from app.db.database import SessionLocal
from app.services.watchdog import sweep_stale_meetings
from app.services.scheduler import trigger_due_meetings
from app.services import rate_limit
from app.observability import configure_logging, init_sentry

# Phase A4. Before anything else in the process does any work: configure_logging
# gives the root logger a handler (uvicorn only configures its own uvicorn.*
# loggers, so without this every logger.info below is dropped), and init_sentry
# is a no-op unless SENTRY_DSN is set. Neither can fail the boot.
configure_logging()
init_sentry("api")

logger = logging.getLogger(__name__)


def _run_with_session(work):
    """
    Runs a sync sweep with a Session created and closed on the same thread.

    The session must not be opened on the event loop thread and closed there
    around an `await`: asyncio.to_thread cannot cancel the worker thread, so
    on shutdown the await raises CancelledError while the sweep is still
    running, and a `finally: db.close()` then closes the Session from the loop
    thread while the worker is mid-query. SQLAlchemy Sessions aren't
    thread-safe, and that raced close surfaced as
    "IllegalStateChangeError: Method 'close()' can't be called here".
    Owning the whole lifecycle inside the worker removes the interleaving.
    """
    db = SessionLocal()
    try:
        work(db)
    finally:
        db.close()


async def _watchdog_loop():
    interval_seconds = settings.watchdog_sweep_interval_minutes * 60
    while True:
        try:
            # Run the sync SQLAlchemy sweep off the event loop thread, same
            # reasoning FastAPI already applies to sync route handlers - this
            # loop runs on the loop thread directly since it's a plain
            # asyncio.create_task, not a request.
            await asyncio.to_thread(_run_with_session, sweep_stale_meetings)
        except asyncio.CancelledError:
            # Normal shutdown - stop quietly rather than logging a traceback.
            raise
        except OperationalError as e:
            # DB connectivity blips (e.g. transient DNS resolution
            # failures reaching the Supabase pooler) are expected and
            # self-heal on the next sweep - a one-line warning is enough,
            # a full traceback every interval is just noise.
            logger.warning("[watchdog] sweep failed: DB connection error (%s) - will retry next cycle", e)
        except Exception:
            logger.exception("[watchdog] sweep failed")
        await asyncio.sleep(interval_seconds)


async def _scheduler_loop():
    interval_seconds = settings.scheduler_sweep_interval_minutes * 60
    while True:
        try:
            await asyncio.to_thread(_run_with_session, trigger_due_meetings)
        except asyncio.CancelledError:
            raise
        except OperationalError as e:
            logger.warning("[scheduler] sweep failed: DB connection error (%s) - will retry next cycle", e)
        except Exception:
            logger.exception("[scheduler] sweep failed")
        await asyncio.sleep(interval_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = []
    if settings.watchdog_enabled:
        tasks.append(asyncio.create_task(_watchdog_loop()))
    if settings.calendar_scheduler_enabled:
        tasks.append(asyncio.create_task(_scheduler_loop()))
    yield
    for task in tasks:
        task.cancel()
    for task in tasks:
        with suppress(asyncio.CancelledError):
            await task
    # The rate limiter opens its Redis client lazily on the first limited
    # request and keeps it for the life of the loop; close it rather than
    # leaving the pool for the OS to tear down on every deploy.
    await rate_limit.aclose()


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

# Database saturation is a 503, not a 500. It has two shapes, and a request
# can hit either depending on where the ceiling is met:
#
# - PoolTimeoutError: this process's pool (db_pool_size in config.py) had no
#   free connection within db_pool_timeout_seconds. The normal shape now that
#   the pool fits under the pooler's cap - demand queues here, not at Postgres.
# - OperationalError carrying EMAXCONNSESSION: Supabase's pooler refused the
#   connection outright because the project's 15 were taken - by another
#   process, a migration, or a psql session outside this budget.
#
# A caller can retry a 503; a 500 with a traceback looks like a bug. This is
# app-level rather than in get_db() because get_db() never touches the
# database - the connection is taken lazily on a route's first query.
DB_BUSY_DETAIL = "The service is busy right now. Please try again in a few seconds."


def _db_busy(reason: str, exc: Exception) -> JSONResponse:
    logger.warning("[db] %s - returning 503: %s", reason, exc)
    return JSONResponse(
        status_code=503,
        content={"detail": DB_BUSY_DETAIL},
        headers={"Retry-After": "2"},
    )


def _is_pooler_rejection(exc: Exception) -> bool:
    return "EMAXCONNSESSION" in str(exc)


@app.exception_handler(PoolTimeoutError)
async def _pool_timeout_handler(request: Request, exc: PoolTimeoutError):
    return _db_busy("connection pool checkout timed out", exc)


@app.exception_handler(OperationalError)
async def _sqlalchemy_operational_error_handler(request: Request, exc: OperationalError):
    if _is_pooler_rejection(exc):
        return _db_busy("database pooler rejected the connection", exc)
    # Any other OperationalError (a DNS blip, a dropped connection) is not
    # saturation. Re-raising leaves it exactly as unhandled as before: a 500,
    # a logged traceback, and a Sentry event.
    raise exc


@app.exception_handler(psycopg.OperationalError)
async def _psycopg_operational_error_handler(request: Request, exc: psycopg.OperationalError):
    # SQLAlchemy normally wraps this in its own OperationalError (handled
    # above); this catches the same rejection if it ever arrives unwrapped.
    if _is_pooler_rejection(exc):
        return _db_busy("database pooler rejected the connection", exc)
    raise exc


app.include_router(meetings.router)
app.include_router(webhooks.router)
app.include_router(chat.router)
app.include_router(users.router)
app.include_router(calendar.router)


@app.get("/health")
def health():
    return {"status": "ok"}
