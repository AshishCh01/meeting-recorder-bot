def detect_platform(meeting_url: str) -> str:
    url = meeting_url.lower()
    if "meet.google.com" in url:
        return "google"
    if "zoom.us" in url:
        return "zoom"
    if "teams.microsoft.com" in url or "teams.live.com" in url:
        return "teams"
    raise ValueError("Unrecognized meeting platform — expected a Google Meet, Zoom, or Teams link")
