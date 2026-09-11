"""
Request authentication (docs/scaling-plan.md, Phase A5).

`get_current_user` runs on essentially every endpoint. Before A5 it did two
network round-trips per request: `supabase.auth.get_user(token)` over HTTPS,
then a `SELECT` (and sometimes an `INSERT`) against Postgres. The first is
gone - tokens are verified locally against Supabase's published ES256 keys -
and the second is cached, because once verification is local *that* query
becomes the dominant cost and leaving it in place gives back most of the win.

Two deliberate behaviour changes, both documented in the plan:

- **Server-side sign-out is no longer visible immediately.** Local
  verification cannot see a revoked session, so a signed-out user's access
  token keeps working until its `exp`. Supabase's default access-token
  lifetime is one hour, and the refresh token *is* revoked, so the window is
  bounded by that lifetime rather than being indefinite. Accepted knowingly;
  `JWT_LOCAL_VERIFICATION_ENABLED=false` restores the old behaviour if it ever
  stops being acceptable.
- **A user row may be up to `user_cache_ttl_seconds` stale.** Only ever in the
  "exists" direction, which is the harmless one.
"""
import hmac
import logging
import threading
import time
from collections import OrderedDict
from typing import Tuple

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.db.database import get_db
from app.db.models import User
from app.db.supabase import supabase
from app.observability import capture_message
from app.services.jwt_verifier import (
    TokenVerificationError,
    claims_to_identity,
    verify_token,
)

logger = logging.getLogger(__name__)

security = HTTPBearer()


# --------------------------------------------------------------------------
# Fallback accounting
#
# The wrapper exists so that a wrong `aud`/`iss` is a log line instead of an
# outage: local verification fails, Supabase is asked, the user stays logged
# in. That makes it silent by construction - it works - which is exactly why
# it has to count itself out loud. A fallback firing on 100% of requests and
# one that never fires look identical from the outside.
#
# This counter reaching zero under real traffic is the criterion for deleting
# the fallback, as a separate change.
# --------------------------------------------------------------------------
_fallback_lock = threading.Lock()
_fallback_count = 0
_fallback_by_reason: dict = {}
_last_sentry_report: dict = {}


def fallback_stats() -> dict:
    """Snapshot of how often local verification has failed in this process."""
    with _fallback_lock:
        return {"total": _fallback_count, "by_reason": dict(_fallback_by_reason)}


def reset_fallback_stats() -> None:
    global _fallback_count
    with _fallback_lock:
        _fallback_count = 0
        _fallback_by_reason.clear()
        _last_sentry_report.clear()


def _record_fallback(reason: str, detail: str) -> None:
    global _fallback_count
    now = time.monotonic()
    with _fallback_lock:
        _fallback_count += 1
        total = _fallback_count
        _fallback_by_reason[reason] = _fallback_by_reason.get(reason, 0) + 1
        for_reason = _fallback_by_reason[reason]
        last = _last_sentry_report.get(reason)
        report = last is None or (now - last) >= settings.auth_fallback_sentry_interval_seconds
        if report:
            _last_sentry_report[reason] = now

    logger.warning(
        "[auth] local verification failed (%s) - falling back to supabase.auth.get_user "
        "(fallback %s total, %s for this reason): %s",
        reason, total, for_reason, detail,
    )

    if report:
        # Rate limited per reason, but the running totals ride along in every
        # event, so the *volume* is legible from a single Sentry issue even
        # though only one event per minute is sent.
        capture_message(
            "[auth] local JWT verification fell back to Supabase ({})".format(reason),
            level="warning",
            tags={"auth_fallback_reason": reason},
            extra={
                "fallbacks_total": total,
                "fallbacks_for_reason": for_reason,
                "detail": detail,
            },
        )


# --------------------------------------------------------------------------
# "This user id has a row in users" - TTL cache
# --------------------------------------------------------------------------
class _UserRowCache:
    """
    Positive-only TTL cache, bounded by insertion order.

    Only caches existence, never absence: a stale positive costs at worst one
    redundant INSERT that the primary key rejects, while a stale negative
    would mean re-running the write path for a row that is already there.
    """

    def __init__(self) -> None:
        self._entries: "OrderedDict[str, float]" = OrderedDict()
        self._lock = threading.Lock()

    def known(self, user_id: str) -> bool:
        now = time.monotonic()
        with self._lock:
            expires_at = self._entries.get(user_id)
            if expires_at is None:
                return False
            if expires_at <= now:
                del self._entries[user_id]
                return False
            return True

    def remember(self, user_id: str) -> None:
        with self._lock:
            self._entries.pop(user_id, None)
            self._entries[user_id] = time.monotonic() + settings.user_cache_ttl_seconds
            while len(self._entries) > settings.user_cache_max_entries:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


user_row_cache = _UserRowCache()


def _ensure_user_row(db: Session, user_id: str, email: str) -> None:
    """
    Keep the local `users` mirror in step with Supabase Auth.

    On a cache hit this touches the database not at all - `Depends(get_db)`
    builds a Session, but SQLAlchemy does not check a connection out of the
    pool until something actually queries.
    """
    if user_row_cache.known(user_id):
        return

    # Every way out of here ends its transaction. `db` is the route's own
    # session (FastAPI hands get_current_user and the route the same get_db),
    # so a SELECT left open would keep a pooled connection checked out for the
    # rest of the request - through whatever slow call the route makes next,
    # and for every user at once right after a restart empties the cache.
    # Rollback, not close(): the route queries this session straight after.
    db_user = db.query(User).filter(User.id == user_id).first()
    if db_user is None:
        db.add(User(id=user_id, email=email))
        try:
            db.commit()
        except IntegrityError:
            # Another request inserted the same user between the SELECT and
            # this INSERT - unremarkable now that a cache miss can happen on
            # several threads at once. Re-check rather than assume, because a
            # unique-email collision lands here too and that one must not be
            # cached as "exists".
            db.rollback()
            exists = db.query(User).filter(User.id == user_id).first() is not None
            db.rollback()  # the re-check's own transaction, on both outcomes
            if not exists:
                raise
    else:
        db.rollback()  # read-only: nothing to commit, just release

    user_row_cache.remember(user_id)


# --------------------------------------------------------------------------
# The dependency
# --------------------------------------------------------------------------
def _identify_via_supabase(token: str) -> Tuple[str, str]:
    """The pre-A5 path: one HTTPS round-trip to Supabase Auth per call."""
    try:
        auth_response = supabase.auth.get_user(token)
        user_data = auth_response.user
        if not user_data:
            raise HTTPException(status_code=401, detail="Invalid authentication credentials")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=401, detail="Could not validate credentials: {}".format(e))

    return user_data.id, (user_data.email or "")


def _identify(token: str) -> Tuple[str, str]:
    if not settings.jwt_local_verification_enabled:
        return _identify_via_supabase(token)

    try:
        return claims_to_identity(verify_token(token))
    except TokenVerificationError as e:
        if not settings.auth_fallback_enabled:
            raise HTTPException(status_code=401, detail="Could not validate credentials")
        _record_fallback(e.reason, str(e))
    except Exception as e:
        # A bug in local verification must not become a 500 on every endpoint.
        # Same treatment as a verification failure, under its own reason so it
        # is distinguishable in the logs and in Sentry.
        if not settings.auth_fallback_enabled:
            raise HTTPException(status_code=401, detail="Could not validate credentials")
        _record_fallback("unexpected_error", "{}: {}".format(type(e).__name__, e))

    return _identify_via_supabase(token)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> str:
    token = credentials.credentials
    user_id, email = _identify(token)
    _ensure_user_row(db, str(user_id), email)
    return str(user_id)


def verify_webhook_token(
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    token = credentials.credentials
    # Use hmac.compare_digest for constant-time comparison to prevent timing attacks
    if not hmac.compare_digest(token, settings.meeting_bot_bearer_token):
        raise HTTPException(status_code=401, detail="Invalid webhook token")
