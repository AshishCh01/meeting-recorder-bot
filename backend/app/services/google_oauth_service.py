import json
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

# calendar.readonly for reading events, userinfo.email so we can show
# *which* Google account is connected in Settings - no other scope
# is requested.
_SCOPES = " ".join([
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
])

# How long a user has to complete Google's consent screen before the
# state param (and the OAuth flow it belongs to) is considered stale.
_STATE_TTL_SECONDS = 600


class GoogleCalendarNotConfigured(Exception):
    """GOOGLE_CLIENT_ID/SECRET or GOOGLE_TOKEN_ENCRYPTION_KEY isn't set (or the key is malformed)."""


class OAuthStateInvalid(Exception):
    """The state param on the OAuth callback failed to verify or has expired."""


class RevokedAccessError(Exception):
    """Google rejected the stored refresh token - the user needs to reconnect."""


def _fernet() -> Fernet:
    if not (settings.google_client_id and settings.google_client_secret and settings.google_token_encryption_key):
        raise GoogleCalendarNotConfigured(
            "Google Calendar integration is not configured - set GOOGLE_CLIENT_ID, "
            "GOOGLE_CLIENT_SECRET and GOOGLE_TOKEN_ENCRYPTION_KEY."
        )
    try:
        return Fernet(settings.google_token_encryption_key.encode())
    except ValueError as e:
        raise GoogleCalendarNotConfigured(f"GOOGLE_TOKEN_ENCRYPTION_KEY is not a valid Fernet key: {e}")


def encrypt_token(token: str) -> str:
    return _fernet().encrypt(token.encode()).decode()


def decrypt_token(blob: str) -> str:
    return _fernet().decrypt(blob.encode()).decode()


def sign_state(user_id: str) -> str:
    """Self-verifying state param: encrypts+timestamps user_id so the
    callback (hit directly by Google's redirect, no Authorization
    header available) can recover who completed consent without a
    server-side "pending state" table."""
    return _fernet().encrypt(json.dumps({"user_id": user_id}).encode()).decode()


def verify_state(state: str) -> str:
    try:
        payload = json.loads(_fernet().decrypt(state.encode(), ttl=_STATE_TTL_SECONDS))
    except InvalidToken:
        raise OAuthStateInvalid("This connection attempt is invalid or has expired - please try connecting again.")
    return payload["user_id"]


def build_auth_url(user_id: str) -> str:
    state = sign_state(user_id)
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_oauth_redirect_uri,
        "response_type": "code",
        "scope": _SCOPES,
        "access_type": "offline",
        # Forces Google to reissue a refresh_token even if this user
        # already has an active grant (e.g. reconnecting after
        # disconnecting) - without this, a repeat consent can come
        # back with no refresh_token at all.
        "prompt": "consent",
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def exchange_code_for_tokens(code: str) -> dict:
    if not (settings.google_client_id and settings.google_client_secret):
        raise GoogleCalendarNotConfigured("Google Calendar integration is not configured.")
    response = httpx.post(
        GOOGLE_TOKEN_URL,
        data={
            "code": code,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": settings.google_oauth_redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def refresh_access_token(refresh_token: str) -> str:
    if not (settings.google_client_id and settings.google_client_secret):
        raise GoogleCalendarNotConfigured("Google Calendar integration is not configured.")
    response = httpx.post(
        GOOGLE_TOKEN_URL,
        data={
            "refresh_token": refresh_token,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "grant_type": "refresh_token",
        },
        timeout=10,
    )
    if response.status_code in (400, 401):
        # Google returns 400 invalid_grant for both an expired and a
        # revoked refresh token - either way, there's nothing to do
        # but ask the user to reconnect.
        raise RevokedAccessError("Google Calendar access was revoked or expired - please reconnect your calendar.")
    response.raise_for_status()
    return response.json()["access_token"]


def fetch_google_email(access_token: str) -> str:
    response = httpx.get(
        GOOGLE_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()["email"]
