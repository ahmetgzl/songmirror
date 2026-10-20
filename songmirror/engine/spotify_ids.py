"""Canonical Spotify track ids for saved mappings and provider writes."""

import re
from urllib.parse import urlsplit


def track_id(value):
    """Accept a catalog id, track URI, or web-player track link."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if re.fullmatch(r"[A-Za-z0-9]+", raw):
        return raw
    match = re.fullmatch(r"spotify:track:([A-Za-z0-9]+)", raw)
    if match:
        return match.group(1)
    try:
        parts = urlsplit(raw if "://" in raw else f"https://{raw}")
        if parts.scheme in {"http", "https"} and parts.hostname in {
            "open.spotify.com", "play.spotify.com",
        } and not parts.username and not parts.password:
            match = re.fullmatch(
                r"/(?:intl-[A-Za-z-]+/)?(?:embed/)?track/([A-Za-z0-9]+)/?",
                parts.path,
            )
            if match:
                return match.group(1)
    except ValueError:
        pass
    raise ValueError("Paste a Spotify track link, track URI, or catalog id.")
