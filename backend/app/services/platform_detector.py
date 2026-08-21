from urllib.parse import urlparse

# hostname -> (platform, allow_subdomains)
# Zoom needs subdomain matching (us02web.zoom.us, app.zoom.us, <company>.zoom.us);
# Google Meet and Teams don't use meeting-link subdomains, so those stay exact.
_ALLOWED_HOSTS = {
    "meet.google.com": ("google", False),
    "zoom.us": ("zoom", True),
    "teams.microsoft.com": ("teams", False),
    "teams.live.com": ("teams", False),
}

_ERROR = "Unrecognized meeting platform — expected a Google Meet, Zoom, or Teams link"


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
