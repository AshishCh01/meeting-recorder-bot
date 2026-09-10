"""
Sentry and logging setup for the two Python entrypoints (docs/scaling-plan.md,
Phase A4).

Two processes, two inits. `app/main.py` runs under uvicorn and `app/worker.py`
runs under the `arq` CLI; the worker does not import main, so each calls
`init_sentry()` for itself with the integration that matches how work arrives.

Why this phase exists at all: A5 (local JWT verification) fails for every user
at once if the expected `aud`/`iss` is wrong, and today nothing would say so
except a user complaining. This is the minimum that makes that deploy
observable.

`SENTRY_DSN` is optional and empty by default, the same way `groq_api_key` and
`jina_api_key` are - unset means Sentry is simply never initialised, and both
processes start exactly as they did before. Local dev and the test suite need
no DSN.
"""
import logging
import re
import sys
from typing import Any, Optional

from app.config import settings

logger = logging.getLogger(__name__)

# What a scrubbed value is replaced with. Matches sentry_sdk's own
# SENSITIVE_DATA_SUBSTITUTE so a scrubbed field looks the same in the UI
# whether we removed it or the SDK did.
SCRUBBED = "[Filtered]"

# Request headers whose *value* must never leave this process. `authorization`
# is the one that matters here: every request to this API carries a Supabase
# JWT in it, and sentry captures request headers by default. The rest are
# included because they are the usual suspects and cost nothing to cover.
_SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "apikey",
    }
)

# Substrings of local-variable names whose values get scrubbed out of captured
# stack frames. sentry_sdk sends frame locals by default (include_local_variables),
# and `token = credentials.credentials` in api/auth.py is a raw JWT sitting in
# a local - the header scrub alone would not catch it.
_SENSITIVE_VAR_SUBSTRINGS = (
    "token",
    "credential",
    "authoriz",
    "secret",
    "password",
    "api_key",
    "apikey",
    "jwt",
    "dsn",
)

# A JWT, anywhere in the event, by shape. Every JWT starts "eyJ" because that
# is base64 of `{"alg...`.
#
# This is the last net rather than the first. It exists because a token does
# not only travel in the header: api/auth.py raises
# HTTPException(detail=f"Could not validate credentials: {e}"), and an
# upstream error string is not somewhere a name-based scrubber can look.
# before_send runs after sentry_sdk has already serialised the event, so by
# this point every leaked copy is a plain string and substitution reaches all
# of them.
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}")

_LOG_FORMAT = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"

_sentry_enabled = False


def configure_logging() -> None:
    """
    Give the root logger a handler so `logger.*` calls actually emit.

    Neither entrypoint had one. uvicorn configures its own `uvicorn.*` loggers
    and arq configures its own `arq.*` logger; both leave root at WARNING with
    no handler, so every `logger.info(...)` in `app.*` was dropped on the
    floor. In the worker that silence had teeth: the retry warning and the
    "gave up after N attempts" error are the only signal that a retry
    happened, so a job that succeeded on attempt 3 looked identical to one
    that succeeded first time.

    `force=True` makes this idempotent - it replaces root's handlers rather
    than stacking a second one on every call.

    stdout, not logging's default stderr, so these lines interleave in order
    with the `print()` calls still left in `app/services/*` (converting those
    is Phase B). PYTHONUNBUFFERED is set in both Dockerfiles, so neither
    stream buffers.
    """
    logging.basicConfig(
        level=settings.log_level.upper(),
        format=_LOG_FORMAT,
        stream=sys.stdout,
        force=True,
    )


def _scrub_mapping(values: dict) -> None:
    for name in list(values):
        if isinstance(name, str) and name.lower() in _SENSITIVE_HEADERS:
            values[name] = SCRUBBED


def _scrub_frame_vars(frame_vars: dict) -> None:
    for name in list(frame_vars):
        if isinstance(name, str) and any(
            marker in name.lower() for marker in _SENSITIVE_VAR_SUBSTRINGS
        ):
            frame_vars[name] = SCRUBBED


def _scrub_stacktraces(containers: Any) -> None:
    for entry in (containers or {}).get("values") or []:
        if not isinstance(entry, dict):
            continue
        frames = (entry.get("stacktrace") or {}).get("frames") or []
        for frame in frames:
            frame_vars = frame.get("vars") if isinstance(frame, dict) else None
            if isinstance(frame_vars, dict):
                _scrub_frame_vars(frame_vars)


def _scrub_strings(node: Any) -> None:
    """Rewrite every string in the event in place, JWTs redacted."""
    if isinstance(node, dict):
        items: Any = node.items()
    elif isinstance(node, list):
        items = enumerate(node)
    else:
        return

    for key, value in items:
        if isinstance(value, str):
            if "eyJ" in value:
                node[key] = _JWT_RE.sub(SCRUBBED, value)
        else:
            _scrub_strings(value)


def scrub_event(event: dict, hint: Optional[dict] = None) -> dict:
    """
    `before_send`: strip credentials out of an event before it is sent.

    sentry_sdk does filter `Authorization` out of `request.headers` itself when
    `send_default_pii` is False, and its EventScrubber covers frame locals
    whose *name* looks sensitive. Neither is sufficient here, which is why this
    was verified against a real captured payload instead of assumed - see
    tests/test_observability.py. A request that raised inside a route arrived
    with the bearer token in roughly twenty frames, under names no denylist
    would flag: `scope.headers`, `conn.headers`, `request.headers`, the
    `functools.partial` in `func`, and FastAPI's `solved_result`. That finding
    is what `include_local_variables=False` in init_sentry answers.

    So this runs three passes, cheapest and most specific first, and the last
    one is shape-based precisely because the first two are name-based and a
    name-based scrubber can only redact what it was told to look for.
    """
    request = event.get("request")
    if isinstance(request, dict):
        headers = request.get("headers")
        if isinstance(headers, dict):
            _scrub_mapping(headers)
        if isinstance(request.get("cookies"), dict):
            request["cookies"] = SCRUBBED

    _scrub_stacktraces(event.get("exception"))
    _scrub_stacktraces(event.get("threads"))
    _scrub_strings(event)
    return event


def init_sentry(component: str, transport: Any = None) -> bool:
    """
    Initialise Sentry for one entrypoint. Returns whether it was enabled.

    `component` is "api" or "worker" and becomes a tag, so the two processes
    are separable in the same Sentry project. The worker additionally gets
    ArqIntegration, which opens an isolation scope per job and captures
    exceptions that escape the job coroutine - that is why nothing here wraps
    `transcribe_job` by hand.

    No DSN, no Sentry: this returns False and the caller carries on.

    `transport` exists so tests can assert against the finished payload this
    exact configuration produces, rather than against a re-creation of it -
    the credential-scrubbing claim is only worth as much as the thing that
    verifies it. Nothing in the app passes it.
    """
    global _sentry_enabled

    dsn = settings.sentry_dsn.strip()
    if not dsn:
        return False

    import sentry_sdk

    integrations = []
    if component == "worker":
        from sentry_sdk.integrations.arq import ArqIntegration

        integrations.append(ArqIntegration())

    sentry_sdk.init(
        dsn=dsn,
        environment=settings.environment,
        # Never send request bodies, cookies, user IPs or job arguments. The
        # user identity we do want is set explicitly in set_meeting_context.
        send_default_pii=False,
        # Fail closed on credentials rather than trying to name them all.
        #
        # Verified, not assumed (tests/test_observability.py): with locals on,
        # a request that raised inside a route put the caller's Supabase JWT
        # into ~20 stack frames - `scope.headers`, `conn.headers`,
        # `request.headers`, the bound `functools.partial` in `func`, FastAPI's
        # `solved_result`. sentry_sdk's own EventScrubber missed every one of
        # them, because it matches variable *names* and none of those are
        # called anything suspicious. Any frame in app/services/* would also
        # hold a `settings` local, whose repr is every API key this app has.
        #
        # The cost is real: tracebacks lose variable values. Sentry still
        # reports file, line, function and source context, which is what
        # A4 needs (an error *rate* for the A5 deploy). The alternative is
        # enumerating every object that might transitively hold a secret, and
        # being wrong about one of them is unrecoverable - the token is in a
        # third party's storage before anyone notices.
        include_local_variables=False,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        before_send=scrub_event,
        integrations=integrations,
        transport=transport,
    )
    sentry_sdk.set_tag("component", component)
    _sentry_enabled = True
    return True


def sentry_enabled() -> bool:
    """True once init_sentry has actually initialised an SDK in this process."""
    return _sentry_enabled


def set_meeting_context(meeting_id: str, user_id: Any = None) -> None:
    """
    Attach the meeting (and its owner) to whatever Sentry captures next.

    Called from the transcription path, which is where an unattributed
    traceback is least useful: `arq-job.args` is redacted under
    `send_default_pii=False`, so without this a failed transcription arrives
    with no indication of *which* meeting failed.

    Inside a worker job this lands on the per-job isolation scope ArqIntegration
    opens, so it does not bleed into the next job.
    """
    if not _sentry_enabled:
        return

    import sentry_sdk

    sentry_sdk.set_tag("meeting_id", str(meeting_id))
    if user_id is not None:
        sentry_sdk.set_tag("user_id", str(user_id))
    sentry_sdk.set_context(
        "meeting",
        {"meeting_id": str(meeting_id), "user_id": str(user_id) if user_id else None},
    )
