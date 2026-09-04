import httpx
from app.config import settings

# Only platforms meeting-bot can actually record. Teams is deliberately
# absent: there is no Teams bot implementation, so routing to it produced a
# meeting stuck in "joining" rather than a clean failure. platform_detector's
# host allowlist already rejects Teams URLs before they reach here.
PLATFORM_ENDPOINTS = {
    "google": "/google/join",
    "zoom": "/zoom/join",
}


def trigger_bot_join(platform: str, meeting_url: str, meeting_id: str, user_id: str, bot_display_name: str) -> dict:
    """
    Calls the standalone meeting-bot service to start joining and recording.
    Fire-and-forget from the API's perspective — the bot notifies us later
    via the webhook once it's done.
    """
    endpoint = PLATFORM_ENDPOINTS.get(platform)
    if endpoint is None:
        # Unreachable via POST /meetings and the calendar flow (both go
        # through platform_detector's allowlist first) - this is here so a
        # future caller that skips that check fails loudly and immediately,
        # rather than leaving the meeting stuck in a non-terminal status.
        raise ValueError(f"No meeting-bot endpoint for platform: {platform!r}")

    response = httpx.post(
        f"{settings.meeting_bot_url}{endpoint}",
        json={
            "url": meeting_url,
            "meetingId": meeting_id,
            "userId": user_id,
            "botDisplayName": bot_display_name,
        },
        headers={
            "Authorization": f"Bearer {settings.meeting_bot_bearer_token}"
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def stop_bot(meeting_id: str) -> dict:
    """
    Asks meeting-bot to abandon whatever it's currently doing for
    meeting_id (waiting for admission, or mid-recording) and shut down
    cleanly. meeting-bot reports the resulting "failed" status back via
    its usual webhook, same as any other in-flight failure.
    """
    response = httpx.post(
        f"{settings.meeting_bot_url}/stop",
        json={"meetingId": meeting_id},
        headers={
            "Authorization": f"Bearer {settings.meeting_bot_bearer_token}"
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json()
