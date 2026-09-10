"""
Local Supabase JWT verification (docs/scaling-plan.md, Phase A5).

Replaces the `supabase.auth.get_user(token)` round-trip that used to run on
every authenticated request. That call put an outbound HTTPS hop in the hot
path of essentially every endpoint: a latency floor nothing else could get
under, this API's availability made strictly worse than Supabase Auth's, and
N x the load on that dependency at N replicas.

This project signs access tokens **asymmetrically, ES256**, with the public
keys published at `${SUPABASE_URL}/auth/v1/.well-known/jwks.json` - confirmed
against the live endpoint rather than assumed. Nothing secret is stored here;
a JWKS is public by construction.

Two things in here exist because getting them wrong is expensive:

1. **`aud` and `iss` are configuration, driven off a real token.** A mismatch
   in either is not a degradation - every token fails, every request 401s and
   every user is logged out at once, the moment it deploys. A unit test cannot
   catch it either, because the fixture would encode whatever was assumed.
2. **The unknown-`kid` refetch is rate limited.** `PyJWKClient.get_signing_key`
   refetches the whole JWKS every time a `kid` misses, with no cooldown. Under
   a key rotation - or a stream of junk tokens carrying a bogus `kid` - that is
   one outbound fetch *per request* aimed at Supabase. See `_SigningKeys`.
"""
import logging
import threading
import time
from typing import Any, List, Optional, Tuple

import jwt
from jwt import PyJWKClient

from app.config import settings

logger = logging.getLogger(__name__)


class TokenVerificationError(Exception):
    """
    Local verification did not produce a trusted set of claims.

    Deliberately one type for every cause - bad signature, expired, wrong
    `aud`, unknown `kid`, malformed. The caller's decision is the same in all
    cases (fall back, then 401), and the distinction that matters for
    debugging is carried in `reason`, a short stable string safe to use as a
    Sentry tag and a log field.
    """

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


class _SigningKeys:
    """
    JWKS fetching with a refetch policy of its own.

    `PyJWKClient` handles the HTTP fetch and caches the key set for `lifespan`
    seconds, which is the part worth reusing. What it does not do is bound the
    *miss* path: `get_signing_key` calls `get_signing_keys(refresh=True)` on
    every `kid` it cannot match, so a thousand requests bearing an unknown
    `kid` are a thousand JWKS fetches. A key rotation would turn into a
    self-inflicted DDoS on Supabase Auth, and so would anyone sending junk
    tokens in a loop.

    So the lookup happens here instead: match against the cached set, and only
    on a miss consider refetching - at most once per
    `jwks_unknown_kid_cooldown_seconds`, under a lock so a burst of concurrent
    misses collapses into one fetch rather than one per thread (uvicorn runs
    sync route handlers on a threadpool, so concurrent is the normal case).

    A real rotation still resolves on the first request that sees the new
    `kid`: the cooldown only suppresses a refetch that has already been tried
    and did not help.
    """

    class _CountingClient(PyJWKClient):
        """PyJWKClient that says how many times it actually hit the network."""

        network_fetch_count = 0

        def fetch_data(self):
            self.network_fetch_count += 1
            return super().fetch_data()

    def __init__(self, uri: str):
        self._client = self._CountingClient(
            uri,
            # Tier 2 (per-kid LRU) off: the lookup below is the cache, and
            # lru_cache would hide the miss path this class exists to control.
            cache_keys=False,
            cache_jwk_set=True,
            lifespan=settings.jwks_cache_seconds,
            timeout=settings.jwks_fetch_timeout_seconds,
        )
        self._lock = threading.Lock()
        self._refetch_not_before = 0.0

    @property
    def network_fetch_count(self) -> int:
        """HTTP fetches of the JWKS. The number gate 3 in the plan is about."""
        return self._client.network_fetch_count

    def _signing_keys(self, refresh: bool):
        return self._client.get_signing_keys(refresh=refresh)

    def get(self, kid: Optional[str]):
        if not kid:
            raise TokenVerificationError("no_kid", "token header has no 'kid'")

        # The common path: whatever PyJWKClient has cached. Only reaches the
        # network when its own lifespan has expired.
        key = PyJWKClient.match_kid(self._signing_keys(refresh=False), kid)
        if key is not None:
            return key

        with self._lock:
            # Re-check inside the lock: a concurrent miss may already have
            # refetched and found it while this thread waited.
            key = PyJWKClient.match_kid(self._signing_keys(refresh=False), kid)
            if key is not None:
                return key

            now = time.monotonic()
            if now < self._refetch_not_before:
                raise TokenVerificationError(
                    "unknown_kid",
                    "no signing key for kid {!r}; refetch on cooldown".format(kid),
                )
            self._refetch_not_before = now + settings.jwks_unknown_kid_cooldown_seconds

            logger.info("[auth] unknown kid %s - refetching JWKS", kid)
            key = PyJWKClient.match_kid(self._signing_keys(refresh=True), kid)
            if key is None:
                raise TokenVerificationError(
                    "unknown_kid",
                    "no signing key for kid {!r} after refetch".format(kid),
                )
            return key


_signing_keys: Optional[_SigningKeys] = None
_signing_keys_lock = threading.Lock()


def signing_keys() -> _SigningKeys:
    """The process-wide JWKS client, built on first use."""
    global _signing_keys
    if _signing_keys is None:
        with _signing_keys_lock:
            if _signing_keys is None:
                _signing_keys = _SigningKeys(jwks_url())
    return _signing_keys


def reset_signing_keys() -> None:
    """Drop the cached client. For tests, and for a config change at runtime."""
    global _signing_keys
    with _signing_keys_lock:
        _signing_keys = None


def jwks_url() -> str:
    if settings.supabase_jwks_url:
        return settings.supabase_jwks_url
    return settings.supabase_url.rstrip("/") + "/auth/v1/.well-known/jwks.json"


def expected_issuer() -> str:
    if settings.jwt_issuer:
        return settings.jwt_issuer
    return settings.supabase_url.rstrip("/") + "/auth/v1"


def allowed_algorithms() -> List[str]:
    return [a.strip() for a in settings.jwt_algorithms.split(",") if a.strip()]


def verify_token(token: str) -> dict:
    """
    Verify a Supabase access token locally and return its claims.

    Raises TokenVerificationError for anything that is not a token this
    service trusts, and nothing else for a bad token - the caller treats every
    failure identically.
    """
    algorithms = allowed_algorithms()

    # Read the header before touching a key. Checking `alg` here against an
    # explicit allow-list is what makes `{"alg": "none"}` a rejection rather
    # than a question - and equally the HS256-confusion trick, where a token
    # is signed with the *public* key as an HMAC secret and an
    # algorithm-agnostic verifier accepts it. jwt.decode(algorithms=...)
    # enforces this too; doing it first means a junk `alg` never reaches the
    # JWKS lookup, and so can never cost a fetch.
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as e:
        raise TokenVerificationError("malformed", "unreadable token header: {}".format(e)) from e

    alg = header.get("alg")
    if alg not in algorithms:
        raise TokenVerificationError(
            "bad_algorithm", "token alg {!r} is not in {}".format(alg, algorithms)
        )

    signing_key = signing_keys().get(header.get("kid"))

    try:
        return jwt.decode(
            token,
            key=signing_key.key,
            algorithms=algorithms,
            audience=settings.jwt_audience,
            issuer=expected_issuer(),
            leeway=settings.jwt_leeway_seconds,
            options={
                "verify_signature": True,
                "verify_exp": True,
                "verify_aud": True,
                "verify_iss": True,
                # A token missing any of these is not one this service can act
                # on, and a missing claim has to be a rejection rather than a
                # silently skipped check.
                "require": ["exp", "sub", "aud", "iss"],
            },
        )
    except jwt.ExpiredSignatureError as e:
        raise TokenVerificationError("expired", str(e)) from e
    except jwt.InvalidAudienceError as e:
        raise TokenVerificationError("bad_audience", str(e)) from e
    except jwt.InvalidIssuerError as e:
        raise TokenVerificationError("bad_issuer", str(e)) from e
    except jwt.InvalidSignatureError as e:
        raise TokenVerificationError("bad_signature", str(e)) from e
    except jwt.MissingRequiredClaimError as e:
        raise TokenVerificationError("missing_claim", str(e)) from e
    except jwt.PyJWTError as e:
        raise TokenVerificationError("invalid", "{}: {}".format(type(e).__name__, e)) from e


def claims_to_identity(claims: dict) -> Tuple[str, str]:
    """
    The two things the app needs out of a verified token.

    `sub` is the Supabase user id. `email` is a top-level claim on Supabase
    access tokens, but is optional here because the users table only needs it
    when inserting a row that does not exist yet.
    """
    user_id: Any = claims.get("sub")
    if not user_id:
        raise TokenVerificationError("missing_claim", "token has no 'sub'")
    return str(user_id), (claims.get("email") or "")
