"""Import the minimal Qobuz web API context from a copied request.

Qobuz's current web player authenticates ``api.json/0.2`` requests with
``X-App-Id`` and ``X-User-Auth-Token``.  The playlist library endpoint infers
the signed-in user from that token, so any authenticated request is enough;
the older query-string form may additionally contain a user id.  Cookies and
all unrelated headers are discarded before persistence.
"""

from __future__ import annotations

import json

from .browser_session import query_values, selected_headers


def parse_web_request(raw: str) -> dict[str, str]:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("paste a signed-in Qobuz API request, request headers, or copied cURL command")

    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        parsed = None
    direct = parsed if isinstance(parsed, dict) else {}
    if isinstance(direct.get("credentials"), dict):
        direct = direct["credentials"]

    query = query_values(raw)
    headers = selected_headers(raw, {"x-app-id", "x-user-auth-token", "x-user-id"})

    def first(*keys):
        for key in keys:
            value = direct.get(key)
            if value not in (None, ""):
                return str(value).strip()
            value = query.get(key)
            if value not in (None, ""):
                return str(value).strip()
            value = headers.get(key.replace("_", "-"))
            if value not in (None, ""):
                return str(value).strip()
        return ""

    credentials: dict[str, str] = {
        "app_id": first("app_id", "x_app_id"),
        "user_auth_token": first("user_auth_token", "x_user_auth_token"),
    }
    user_id = first("user_id", "x_user_id")
    if user_id:
        credentials["user_id"] = user_id
    missing = [key for key, value in credentials.items() if not value]
    if missing:
        readable = {"app_id": "X-App-Id", "user_auth_token": "X-User-Auth-Token"}
        raise ValueError(
            "the copied request is missing " + ", ".join(readable[key] for key in missing)
            + "; copy headers or cURL from any signed-in api.json/0.2 request"
        )
    if any("\r" in value or "\n" in value for value in credentials.values()):
        raise ValueError("Qobuz credentials contain a line break")
    return credentials


def serialize_web_request(raw: str) -> str:
    return json.dumps(parse_web_request(raw), separators=(",", ":"), sort_keys=True)
