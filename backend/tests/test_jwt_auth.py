"""
Phase A5: local JWT verification.

The highest-risk change in the plan. A wrong `aud` or `iss` does not degrade
anything - every token fails, every request 401s and every user is logged out
simultaneously the moment it deploys. These tests cannot catch that class of
mistake on their own and should not be read as if they can: the tokens below
are signed by a key this file generates, so they carry whatever claims this
file decided on. The `aud`/`iss` values in `app/config.py` were read off a
real Supabase access token instead; see docs/scaling-plan.md A5.

What these *do* pin down is everything that is not a guess about Supabase:
each rejection path, the `alg: none` hole, the JWKS refetch policy, the
fallback wrapper, and the user-row cache.

No network: `urllib.request.urlopen` is stubbed, which is also how the JWKS
fetches get counted.
"""
import base64
import hashlib
import hmac
import json
import time
import urllib.request
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api import auth as auth_module
from app.api.auth import get_current_user
from app.config import settings
from app.db.models import User
from app.services import jwt_verifier

ISSUER = "https://stub.supabase.co/auth/v1"
JWKS_URL = "https://stub.supabase.co/auth/v1/.well-known/jwks.json"
AUDIENCE = "authenticated"
KID = "59cc524a-1111-4222-8333-444455556666"
OTHER_KID = "rotated-key-0000-0000-0000-000000000000"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _public_jwk(private_key, kid: str) -> dict:
    """The ES256 public half, in the shape a JWKS publishes it."""
    numbers = private_key.public_key().public_numbers()
    return {
        "kty": "EC",
        "crv": "P-256",
        "alg": "ES256",
        "use": "sig",
        "kid": kid,
        "x": _b64(numbers.x.to_bytes(32, "big")),
        "y": _b64(numbers.y.to_bytes(32, "big")),
    }


class _FakeResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode()

    def read(self, *args):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def jwks(monkeypatch):
    """
    A live-ish JWKS: one signing key, served by a stubbed urlopen that counts
    how many times it was asked. `fetches` is the number gate 3 is about.
    """
    signing_key = ec.generate_private_key(ec.SECP256R1())
    impostor_key = ec.generate_private_key(ec.SECP256R1())

    state = {"fetches": 0, "keys": [_public_jwk(signing_key, KID)]}

    def fake_urlopen(request, *args, **kwargs):
        state["fetches"] += 1
        return _FakeResponse({"keys": state["keys"]})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    monkeypatch.setattr(settings, "supabase_jwks_url", JWKS_URL)
    monkeypatch.setattr(settings, "jwt_issuer", ISSUER)
    monkeypatch.setattr(settings, "jwt_audience", AUDIENCE)
    monkeypatch.setattr(settings, "jwt_algorithms", "ES256")
    monkeypatch.setattr(settings, "jwt_local_verification_enabled", True)
    monkeypatch.setattr(settings, "auth_fallback_enabled", True)
    monkeypatch.setattr(settings, "jwks_cache_seconds", 3600)
    monkeypatch.setattr(settings, "jwks_unknown_kid_cooldown_seconds", 60)
    monkeypatch.setattr(settings, "user_cache_ttl_seconds", 300)

    jwt_verifier.reset_signing_keys()
    auth_module.user_row_cache.clear()
    auth_module.reset_fallback_stats()

    state["signing_key"] = signing_key
    state["impostor_key"] = impostor_key
    yield state

    jwt_verifier.reset_signing_keys()
    auth_module.user_row_cache.clear()
    auth_module.reset_fallback_stats()


def make_token(jwks, *, key=None, kid=KID, aud=AUDIENCE, iss=ISSUER,
               sub=None, email="a5@example.com", exp_delta=3600, **extra):
    now = int(time.time())
    claims = {
        "sub": sub or str(uuid.uuid4()),
        "aud": aud,
        "iss": iss,
        "iat": now,
        "exp": now + exp_delta,
        "email": email,
        "role": "authenticated",
    }
    claims.update(extra)
    return jwt.encode(
        claims,
        key or jwks["signing_key"],
        algorithm="ES256",
        headers={"kid": kid},
    )


def alg_none_token(sub: str) -> str:
    """
    An unsigned token, hand-built.

    Not produced through PyJWT's encoder on purpose: the attack is a raw
    string an attacker sends, and it must be rejected on its own terms rather
    than only when a library declines to generate one.
    """
    header = _b64(json.dumps({"alg": "none", "typ": "JWT", "kid": KID}).encode())
    payload = _b64(json.dumps({
        "sub": sub,
        "aud": AUDIENCE,
        "iss": ISSUER,
        "exp": int(time.time()) + 3600,
        "email": "attacker@example.com",
    }).encode())
    return "{}.{}.".format(header, payload)


# ---------------------------------------------------------------------------
# verify_token, directly
# ---------------------------------------------------------------------------
def test_valid_token_returns_sub_and_email(jwks):
    sub = str(uuid.uuid4())
    claims = jwt_verifier.verify_token(make_token(jwks, sub=sub))
    assert claims["sub"] == sub
    assert jwt_verifier.claims_to_identity(claims) == (sub, "a5@example.com")


@pytest.mark.parametrize(
    "kwargs,expected_reason",
    [
        ({"exp_delta": -3600}, "expired"),
        ({"aud": "not-authenticated"}, "bad_audience"),
        ({"iss": "https://evil.example.com/auth/v1"}, "bad_issuer"),
    ],
)
def test_rejection_reasons(jwks, kwargs, expected_reason):
    with pytest.raises(jwt_verifier.TokenVerificationError) as excinfo:
        jwt_verifier.verify_token(make_token(jwks, **kwargs))
    assert excinfo.value.reason == expected_reason


def test_token_signed_by_another_key_is_rejected(jwks):
    """Right `kid`, right claims, wrong private key."""
    token = make_token(jwks, key=jwks["impostor_key"])
    with pytest.raises(jwt_verifier.TokenVerificationError) as excinfo:
        jwt_verifier.verify_token(token)
    assert excinfo.value.reason == "bad_signature"


def test_alg_none_is_rejected_before_any_key_lookup(jwks):
    """
    The classic JWT bug: a verifier that trusts the token's own `alg` header
    accepts an unsigned token. Rejection happens on the allow-list check, so
    it also costs no JWKS fetch - a junk `alg` must not be a way to make this
    service call Supabase.
    """
    fetches_before = jwks["fetches"]
    with pytest.raises(jwt_verifier.TokenVerificationError) as excinfo:
        jwt_verifier.verify_token(alg_none_token(str(uuid.uuid4())))
    assert excinfo.value.reason == "bad_algorithm"
    assert jwks["fetches"] == fetches_before


def test_hs256_signed_with_the_public_key_is_rejected(jwks):
    """
    Algorithm confusion: the JWKS is public, so if an HMAC algorithm were ever
    accepted the published key would double as a valid shared secret.
    """
    from cryptography.hazmat.primitives import serialization

    pem = jwks["signing_key"].public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    now = int(time.time())
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": KID}).encode())
    payload = _b64(json.dumps(
        {"sub": str(uuid.uuid4()), "aud": AUDIENCE, "iss": ISSUER, "exp": now + 3600}
    ).encode())
    # Hand-rolled rather than via jwt.encode, which refuses to use a PEM as an
    # HMAC secret. That refusal is PyJWT protecting the *signing* side; the
    # attack is a raw string arriving over HTTP, so the test has to send one.
    signature = _b64(
        hmac.new(pem, "{}.{}".format(header, payload).encode(), hashlib.sha256).digest()
    )
    forged = "{}.{}.{}".format(header, payload, signature)

    with pytest.raises(jwt_verifier.TokenVerificationError) as excinfo:
        jwt_verifier.verify_token(forged)
    assert excinfo.value.reason == "bad_algorithm"


def test_token_missing_required_claims_is_rejected(jwks):
    now = int(time.time())
    token = jwt.encode(
        {"aud": AUDIENCE, "iss": ISSUER, "exp": now + 3600},   # no sub
        jwks["signing_key"],
        algorithm="ES256",
        headers={"kid": KID},
    )
    with pytest.raises(jwt_verifier.TokenVerificationError) as excinfo:
        jwt_verifier.verify_token(token)
    assert excinfo.value.reason == "missing_claim"


# ---------------------------------------------------------------------------
# Gate 3: the JWKS refetch policy
# ---------------------------------------------------------------------------
def test_steady_state_does_not_refetch_the_jwks(jwks):
    for _ in range(25):
        jwt_verifier.verify_token(make_token(jwks))
    assert jwks["fetches"] == 1
    assert jwt_verifier.signing_keys().network_fetch_count == 1


def test_unknown_kid_refetches_exactly_once_not_once_per_request(jwks):
    """
    The gate. `PyJWKClient.get_signing_key` refetches on *every* kid miss with
    no cooldown, so a burst of tokens carrying a bogus kid is a burst of
    outbound requests at Supabase Auth - a key rotation, or anyone sending
    junk in a loop, becomes a self-inflicted DDoS. `_SigningKeys` puts a
    cooldown in front of that.
    """
    jwt_verifier.verify_token(make_token(jwks))       # warm the cache: 1 fetch
    assert jwks["fetches"] == 1

    for _ in range(50):
        with pytest.raises(jwt_verifier.TokenVerificationError) as excinfo:
            jwt_verifier.verify_token(make_token(jwks, kid=OTHER_KID))
        assert excinfo.value.reason == "unknown_kid"

    assert jwks["fetches"] == 2, "50 unknown-kid requests must cost one refetch"
    assert jwt_verifier.signing_keys().network_fetch_count == 2


def test_a_real_key_rotation_is_picked_up_on_the_first_request(jwks):
    """
    The cooldown must not delay a rotation - it only suppresses a refetch that
    was already tried and did not help.
    """
    jwt_verifier.verify_token(make_token(jwks))
    assert jwks["fetches"] == 1

    rotated = ec.generate_private_key(ec.SECP256R1())
    jwks["keys"].append(_public_jwk(rotated, OTHER_KID))

    claims = jwt_verifier.verify_token(make_token(jwks, key=rotated, kid=OTHER_KID))
    assert claims["aud"] == AUDIENCE
    assert jwks["fetches"] == 2


def test_cooldown_expiry_allows_another_refetch(jwks, monkeypatch):
    monkeypatch.setattr(settings, "jwks_unknown_kid_cooldown_seconds", 0)
    jwt_verifier.verify_token(make_token(jwks))
    for _ in range(3):
        with pytest.raises(jwt_verifier.TokenVerificationError):
            jwt_verifier.verify_token(make_token(jwks, kid=OTHER_KID))
    # Cooldown of zero is the degenerate case: back to one fetch per miss.
    # Asserted so the cooldown is demonstrably the thing doing the work above.
    assert jwks["fetches"] == 4


# ---------------------------------------------------------------------------
# The dependency, end to end through FastAPI
# ---------------------------------------------------------------------------
class _StubSupabaseUser:
    def __init__(self, user_id, email):
        self.id = user_id
        self.email = email


class _StubAuth:
    """Stands in for supabase.auth, and records whether it was called."""

    def __init__(self, user=None, error=None):
        self.calls = 0
        self._user = user
        self._error = error

    def get_user(self, token):
        self.calls += 1
        if self._error:
            raise self._error
        return type("Resp", (), {"user": self._user})()


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()

    @app.get("/whoami")
    def whoami(user_id: str = Depends(get_current_user)):
        return {"user_id": user_id}

    return TestClient(app, raise_server_exceptions=False)


def _stub_supabase(monkeypatch, stub_auth):
    monkeypatch.setattr(
        auth_module, "supabase", type("C", (), {"auth": stub_auth})()
    )


def test_valid_token_authenticates_and_creates_the_user_row(jwks, client, db, monkeypatch):
    stub = _StubAuth(error=RuntimeError("supabase must not be called"))
    _stub_supabase(monkeypatch, stub)

    sub = str(uuid.uuid4())
    token = make_token(jwks, sub=sub, email="new-user@example.com")

    r = client.get("/whoami", headers={"Authorization": "Bearer " + token})
    assert r.status_code == 200
    assert r.json() == {"user_id": sub}
    assert stub.calls == 0, "a valid token must not touch Supabase Auth"

    row = db.query(User).filter(User.id == sub).first()
    assert row is not None and row.email == "new-user@example.com"


@pytest.mark.parametrize(
    "token_kwargs",
    [
        {"exp_delta": -3600},
        {"aud": "not-authenticated"},
        {"iss": "https://evil.example.com/auth/v1"},
    ],
    ids=["expired", "wrong_aud", "wrong_iss"],
)
def test_rejected_tokens_return_401(jwks, client, monkeypatch, token_kwargs):
    """
    With the fallback on, a rejected token is asked about upstream before it
    is refused - so Supabase is consulted and the answer is still 401.
    """
    _stub_supabase(monkeypatch, _StubAuth(user=None))
    r = client.get(
        "/whoami",
        headers={"Authorization": "Bearer " + make_token(jwks, **token_kwargs)},
    )
    assert r.status_code == 401


def test_wrong_signature_returns_401(jwks, client, monkeypatch):
    _stub_supabase(monkeypatch, _StubAuth(user=None))
    token = make_token(jwks, key=jwks["impostor_key"])
    r = client.get("/whoami", headers={"Authorization": "Bearer " + token})
    assert r.status_code == 401


def test_alg_none_returns_401(jwks, client, monkeypatch):
    _stub_supabase(monkeypatch, _StubAuth(user=None))
    r = client.get(
        "/whoami",
        headers={"Authorization": "Bearer " + alg_none_token(str(uuid.uuid4()))},
    )
    assert r.status_code == 401


def test_garbage_token_returns_401(jwks, client, monkeypatch):
    _stub_supabase(monkeypatch, _StubAuth(user=None))
    r = client.get("/whoami", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# The fallback wrapper
# ---------------------------------------------------------------------------
def test_fallback_keeps_a_user_logged_in_when_local_verification_fails(
    jwks, client, db, monkeypatch
):
    """
    The whole point of the wrapper: a token this service cannot verify - a
    legacy HS256 one still in flight after the migration to asymmetric keys,
    or a wrong `aud` in config - is asked about upstream instead of becoming
    an outage.
    """
    sub = str(uuid.uuid4())
    stub = _StubAuth(user=_StubSupabaseUser(sub, "legacy@example.com"))
    _stub_supabase(monkeypatch, stub)

    now = int(time.time())
    legacy = jwt.encode(
        {"sub": sub, "aud": AUDIENCE, "iss": ISSUER, "exp": now + 3600},
        "legacy-hs256-secret",
        algorithm="HS256",
        headers={"kid": KID},
    )

    r = client.get("/whoami", headers={"Authorization": "Bearer " + legacy})
    assert r.status_code == 200
    assert r.json() == {"user_id": sub}
    assert stub.calls == 1

    stats = auth_module.fallback_stats()
    assert stats["total"] == 1
    assert stats["by_reason"] == {"bad_algorithm": 1}


def test_fallback_reports_to_sentry_with_the_running_total(jwks, client, monkeypatch):
    """
    A fallback that fires on 100% of requests looks exactly like one that
    never fires unless it says so somewhere a human is watching. Sentry
    reports are rate limited per reason, so the totals travel inside the
    event rather than as event volume.
    """
    reported = []
    monkeypatch.setattr(
        auth_module,
        "capture_message",
        lambda message, level="warning", tags=None, extra=None: reported.append(
            (message, level, tags, extra)
        ),
    )
    sub = str(uuid.uuid4())
    _stub_supabase(monkeypatch, _StubAuth(user=_StubSupabaseUser(sub, "x@example.com")))

    for _ in range(5):
        client.get(
            "/whoami",
            headers={"Authorization": "Bearer " + make_token(jwks, aud="wrong")},
        )

    assert len(reported) == 1, "rate limited to one event per reason per interval"
    _, level, tags, extra = reported[0]
    assert level == "warning"
    assert tags == {"auth_fallback_reason": "bad_audience"}
    assert extra["fallbacks_total"] == 1
    assert auth_module.fallback_stats()["total"] == 5


def test_fallback_disabled_rejects_without_calling_supabase(jwks, client, monkeypatch):
    """The follow-up change, once the counter has sat at zero."""
    monkeypatch.setattr(settings, "auth_fallback_enabled", False)
    stub = _StubAuth(user=None)
    _stub_supabase(monkeypatch, stub)

    r = client.get(
        "/whoami",
        headers={"Authorization": "Bearer " + make_token(jwks, aud="wrong")},
    )
    assert r.status_code == 401
    assert stub.calls == 0


def test_kill_switch_restores_the_pre_a5_path(jwks, client, db, monkeypatch):
    """
    JWT_LOCAL_VERIFICATION_ENABLED=false must go straight to Supabase without
    verifying anything locally - the revert for this phase, with no rebuild.
    """
    monkeypatch.setattr(settings, "jwt_local_verification_enabled", False)
    sub = str(uuid.uuid4())
    stub = _StubAuth(user=_StubSupabaseUser(sub, "killswitch@example.com"))
    _stub_supabase(monkeypatch, stub)

    # A token local verification would have rejected outright.
    r = client.get(
        "/whoami",
        headers={"Authorization": "Bearer " + alg_none_token(sub)},
    )
    assert r.status_code == 200
    assert stub.calls == 1
    assert auth_module.fallback_stats()["total"] == 0, "not a fallback - the switch is off"


# ---------------------------------------------------------------------------
# The user-row cache
# ---------------------------------------------------------------------------
def test_user_row_lookup_is_cached_after_the_first_request(jwks, client, db, monkeypatch):
    """
    Once verification stops going over the network, this SELECT is the
    dominant per-request cost. Skipping the cache makes A5's win much smaller
    than expected, so it is asserted rather than assumed.
    """
    sub = str(uuid.uuid4())
    _stub_supabase(monkeypatch, _StubAuth(error=RuntimeError("must not be called")))
    token = make_token(jwks, sub=sub)

    queries = {"count": 0}
    real_ensure = auth_module._ensure_user_row

    from sqlalchemy import event
    from app.db import database

    def count_queries(conn, cursor, statement, params, context, executemany):
        if "users" in statement.lower():
            queries["count"] += 1

    event.listen(database.engine, "before_cursor_execute", count_queries)
    try:
        for _ in range(10):
            assert client.get(
                "/whoami", headers={"Authorization": "Bearer " + token}
            ).status_code == 200
    finally:
        event.remove(database.engine, "before_cursor_execute", count_queries)

    # First request: SELECT (miss) + INSERT. The other nine: nothing at all.
    assert queries["count"] == 2, "expected 1 SELECT + 1 INSERT, got {}".format(
        queries["count"]
    )
    assert real_ensure is auth_module._ensure_user_row


def test_user_cache_entry_expires(jwks, monkeypatch):
    monkeypatch.setattr(settings, "user_cache_ttl_seconds", 0)
    auth_module.user_row_cache.remember("u-1")
    assert auth_module.user_row_cache.known("u-1") is False


def test_user_cache_is_bounded(jwks, monkeypatch):
    monkeypatch.setattr(settings, "user_cache_max_entries", 10)
    for i in range(50):
        auth_module.user_row_cache.remember("user-{}".format(i))
    assert len(auth_module.user_row_cache) == 10
    assert auth_module.user_row_cache.known("user-0") is False
    assert auth_module.user_row_cache.known("user-49") is True
