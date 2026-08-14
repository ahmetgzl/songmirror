"""Minimized TIDAL web-player authorization import.

The signed-in player calls TIDAL's JSON:API with a Bearer token.  SongMirror
stores only that token and the two-letter catalog country, never browser
cookies or playback request data.
"""

from __future__ import annotations

import json
import re

from .browser_session import bearer_from, bearer_is_expired, query_values, selected_headers


def parse_web_headers(raw: str) -> dict[str, str]:
    token = bearer_from(raw)
    if bearer_is_expired(token):
        raise ValueError("the pasted TIDAL web-player Bearer token is expired")

    try:
        direct = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        direct = {}
    if not isinstance(direct, dict):
        direct = {}
    query = query_values(raw)
    headers = selected_headers(raw, {"x-tidal-country-code", "tidal-country-code"})
    country = (
        direct.get("country_code")
        or direct.get("countryCode")
        or query.get("countryCode")
        or query.get("country_code")
        or headers.get("x-tidal-country-code")
        or headers.get("tidal-country-code")
        or "US"
    )
    country = str(country).strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", country):
        raise ValueError("TIDAL country code must be two letters, for example US")
    return {"authorization": f"Bearer {token}", "country_code": country}


def serialize_web_headers(raw: str) -> str:
    return json.dumps(parse_web_headers(raw), separators=(",", ":"), sort_keys=True)
