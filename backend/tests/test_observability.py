"""
Phase A4: proves the Sentry wiring does not ship credentials to Sentry.

Every request to this API carries a Supabase JWT in its `Authorization`
header, and sentry_sdk captures request headers by default. The SDK does
filter that header itself when `send_default_pii` is False - but a leak that
is prevented only by somebody else's default is one changed default away, and
the failure is silent and retroactive (the token is already in a third party's
storage before anyone notices). So `app.observability.scrub_event` runs as
`before_send`, and these tests assert against the finished payload that the
real `init_sentry()` configuration produces.

No DSN and no network: `init_sentry` is handed a transport that appends the
event to a list instead of sending it. The DSN is a syntactically valid
placeholder pointing at `.invalid`, which cannot resolve.
"""
import json

import pytest
import sentry_sdk
from fastapi import Depends, FastAPI
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.testclient import TestClient
from sentry_sdk.transport import Transport

from app import observability
from app.config import settings
from app.observability import SCRUBBED, init_sentry, scrub_event

# Shaped like a Supabase access token so a substring search for it is
# meaningful. Not a real credential - the payload decodes to {"sub":"u1"} and
# the signature is the literal string "not-a-real-signature".
FAKE_JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiJ1MSJ9"
    ".bm90LWEtcmVhbC1zaWduYXR1cmU"
)

PLACEHOLDER_DSN = "https://publickey@o0.ingest.invalid/1"


class CapturingTransport(Transport):
    """
    Keeps the envelope instead of sending it.

    This is the last stop before the wire - `before_send` has already run, so
    what lands in `self.events` is exactly the payload that would have left the
    process. That is the point: what a Sentry project's UI shows is what
    survived their server-side processing, which is not the same claim.
    """

    def __init__(self, options=None):
        super().__init__(options)
        self.events = []

    def capture_envelope(self, envelope):
        for item in envelope.items:
            if item.headers.get("type") == "event":
                self.events.append(item.payload.json)


@pytest.fixture
def captured_events(monkeypatch):
    """
    Initialise Sentry exactly the way the app does, but with the transport
    above. Tears the client back down so an initialised SDK cannot leak into
    the rest of the suite.
    """
    transport = CapturingTransport()
    monkeypatch.setattr(settings, "sentry_dsn", PLACEHOLDER_DSN)
    enabled = init_sentry("api", transport=transport)
    assert enabled, "init_sentry should report enabled when a DSN is set"
    try:
        yield transport.events
    finally:
        sentry_sdk.get_isolation_scope().clear()
        sentry_sdk.init(dsn="")
        observability._sentry_enabled = False


def test_disabled_without_dsn(monkeypatch):
    """The default path: no DSN, no Sentry, no error."""
    monkeypatch.setattr(settings, "sentry_dsn", "")
    assert init_sentry("api") is False
    assert observability.sentry_enabled() is False


def test_blank_dsn_is_treated_as_unset(monkeypatch):
    """A DSN of whitespace is a typo, not a configuration - it must not init."""
    monkeypatch.setattr(settings, "sentry_dsn", "   ")
    assert init_sentry("worker") is False


def test_scrub_event_filters_authorization_header():
    event = {"request": {"headers": {"Authorization": f"Bearer {FAKE_JWT}"}}}
    assert scrub_event(event)["request"]["headers"]["Authorization"] == SCRUBBED


def test_scrub_event_is_case_insensitive_and_keeps_harmless_headers():
    event = {
        "request": {
            "headers": {
                "authorization": f"Bearer {FAKE_JWT}",
                "Cookie": "sb-access-token=x",
                "User-Agent": "pytest",
            }
        }
    }
    headers = scrub_event(event)["request"]["headers"]
    assert headers["authorization"] == SCRUBBED
    assert headers["Cookie"] == SCRUBBED
    assert headers["User-Agent"] == "pytest"


def test_scrub_event_filters_credentials_out_of_frame_locals():
    """
    Defence in depth. `include_local_variables=False` means a real event has no
    `vars` at all, but the name scrub stays covered so that flipping locals back
    on for a debugging session does not silently re-open the leak.
    """
    event = {
        "exception": {
            "values": [
                {
                    "stacktrace": {
                        "frames": [
                            {
                                "function": "get_current_user",
                                "vars": {
                                    "token": FAKE_JWT,
                                    "credentials": f"HTTPAuthorizationCredentials({FAKE_JWT})",
                                    "meeting_id": "abc-123",
                                },
                            }
                        ]
                    }
                }
            ]
        }
    }
    frame_vars = scrub_event(event)["exception"]["values"][0]["stacktrace"]["frames"][0]["vars"]
    assert frame_vars["token"] == SCRUBBED
    assert frame_vars["credentials"] == SCRUBBED
    # Not a credential, and it is the whole point of set_meeting_context.
    assert frame_vars["meeting_id"] == "abc-123"


def test_scrub_event_redacts_a_jwt_anywhere_in_the_payload():
    """
    The shape-based pass, which is what catches the leaks a name-based one
    cannot see. api/auth.py raises
    HTTPException(detail=f"Could not validate credentials: {e}") - if the
    upstream error echoes the token, it lands in the exception *message*.
    """
    event = {
        "exception": {
            "values": [
                {
                    "type": "HTTPException",
                    "value": f"Could not validate credentials: bad jwt {FAKE_JWT}",
                }
            ]
        },
        "breadcrumbs": {"values": [{"message": f"GET /meetings auth={FAKE_JWT}"}]},
    }
    scrubbed = json.dumps(scrub_event(event))
    assert FAKE_JWT not in scrubbed
    assert SCRUBBED in scrubbed
    # Only the token is removed, not the surrounding message.
    assert "Could not validate credentials" in scrubbed


def test_local_variables_are_not_collected(captured_events):
    """
    The structural half of the fix. With locals on, a request that raised in a
    route carried the caller's JWT in ~20 frames under names no denylist
    flags (`scope.headers`, `conn.headers`, `func`, `solved_result`), and any
    frame in app/services/* would additionally hold a `settings` local whose
    repr is every API key this app has.
    """
    assert sentry_sdk.get_client().options["include_local_variables"] is False


def test_api_exception_event_carries_no_token(captured_events):
    """
    End to end through the real integration: a request with a Supabase-shaped
    bearer token blows up in a route, and the token appears nowhere in the
    event that would have been sent.
    """
    security = HTTPBearer()
    # Built after init_sentry so the Starlette integration's patched
    # middleware stack applies to it.
    app = FastAPI()

    @app.get("/boom")
    def boom(credentials: HTTPAuthorizationCredentials = Depends(security)):
        token = credentials.credentials  # noqa: F841 - deliberately a local
        raise RuntimeError("phase A4 smoke test")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/boom", headers={"Authorization": f"Bearer {FAKE_JWT}"})
    assert response.status_code == 500

    assert captured_events, "the unhandled exception should have produced an event"
    event = captured_events[-1]
    # The whole payload, not just the header: this is the assertion that
    # caught the frame-locals leak in the first place.
    assert FAKE_JWT not in json.dumps(event, default=str)
    # Starlette lowercases header names on the way in.
    headers = {k.lower(): v for k, v in event["request"]["headers"].items()}
    assert headers["authorization"] == SCRUBBED
    frames = event["exception"]["values"][-1]["stacktrace"]["frames"]
    assert all("vars" not in frame for frame in frames)
    assert event["environment"] == settings.environment
    assert event["tags"]["component"] == "api"


def test_meeting_context_tags_the_event(captured_events):
    """
    Gate 3's requirement, at the unit level: meeting_id and user_id ride along
    on whatever Sentry captures next. `arq-job.args` is redacted under
    send_default_pii=False, so without this a failed transcription arrives
    with no indication of which meeting it was.
    """
    observability.set_meeting_context("m-1", "u-9")
    try:
        raise RuntimeError("transcription blew up")
    except RuntimeError as e:
        sentry_sdk.capture_exception(e)

    event = captured_events[-1]
    assert event["tags"]["meeting_id"] == "m-1"
    assert event["tags"]["user_id"] == "u-9"
    assert event["contexts"]["meeting"]["meeting_id"] == "m-1"
