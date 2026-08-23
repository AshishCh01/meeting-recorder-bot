from urllib.parse import urlparse

# hostname -> (platform, allow_subdomains)
# Zoom needs subdomain matching (us02web.zoom.us, app.zoom.us, <company>.zoom.us);
# Google Meet doesn't use meeting-link subdomains, so it stays exact.
# Teams is intentionally not listed - meeting-bot has no Teams bot
# implementation, so allowing it here would let a submitted Teams URL
# create a meeting that gets stuck in "joining" until the watchdog
# times it out. Add it back once a Teams bot actually exists.
_ALLOWED_HOSTS = {
    "meet.google.com": ("google", False),
    "zoom.us": ("zoom", True),
}

_ERROR = "Unrecognized meeting platform — expected a Google Meet or Zoom link"


def detect_platform(meeting_url: str) -> str:
    try:
        parsed = urlparse(meeting_url)
        hostname = (parsed.hostname or "").lower()
    except ValueError:
        raise ValueError(_ERROR)

    if parsed.scheme == "https" and hostname:
        for host, (platform, allow_subdomains) in _ALLOWED_HOSTS.items():
            if hostname == host or (allow_subdomains and hostname.endswith(f".{host}")):
                return platform

    raise ValueError(_ERROR)
