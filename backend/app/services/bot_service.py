import httpx
from app.config import settings

PLATFORM_ENDPOINTS = {
    "google": "/google/join",
    "zoom": "/zoom/join",
    "teams": "/teams/join",
}


def trigger_bot_join(platform: str, meeting_url: str, meeting_id: str) -> dict:
    """
    Calls the standalone meeting-bot service to start joining and recording.
    Fire-and-forget from the API's perspective — the bot notifies us later
    via the webhook once it's done.
    """
    endpoint = PLATFORM_ENDPOINTS[platform]

    response = httpx.post(
        f"{settings.meeting_bot_url}{endpoint}",
        json={
            "url": meeting_url,
            "meetingId": meeting_id,
            "bearerToken": settings.meeting_bot_bearer_token,
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json()
