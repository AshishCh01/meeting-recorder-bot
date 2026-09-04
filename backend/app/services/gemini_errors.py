"""
Shared classification of transient Gemini / network failures.

This predicate was previously copy-pasted in five places across
transcription_service.py and embedding_service.py, which is exactly how 504
came to be missing from all of them: Gemini returns 504 "Deadline expired
before operation could complete" under load, which is every bit as transient
as a 429 or 503 - but because it wasn't in the tuple, a 504 skipped both the
retry loop and the Sarvam/Jina provider fallback and failed the meeting
outright, leaving the user to hit Retry by hand.

Keeping it in one place means the next status code only has to be added once.
"""
import httpx
from google.genai import errors

# 429 rate limited, 503 overloaded, 504 upstream deadline exceeded - all
# worth retrying. Deliberately excludes 4xx like 400/401: retrying a
# malformed request or a bad API key just burns time and quota.
TRANSIENT_STATUS_CODES = (429, 503, 504)

# Connection-level failures carry no status code and are always transient.
TRANSIENT_NETWORK_ERRORS = (
    httpx.ConnectError,
    httpx.TimeoutException,
    httpx.RemoteProtocolError,
)

# Every except clause that uses is_transient() should catch exactly this.
TRANSIENT_EXCEPTIONS = (errors.APIError,) + TRANSIENT_NETWORK_ERRORS


def is_transient(e: Exception) -> bool:
    """True if `e` is worth retrying or failing over to another provider."""
    if isinstance(e, errors.APIError):
        return e.code in TRANSIENT_STATUS_CODES
    return isinstance(e, TRANSIENT_NETWORK_ERRORS)


def error_code_str(e: Exception) -> str:
    """A short label for logs: the HTTP status if there is one, else the type."""
    return str(e.code) if isinstance(e, errors.APIError) else type(e).__name__
