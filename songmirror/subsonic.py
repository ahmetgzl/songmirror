"""Subsonic/OpenSubsonic JSON transport with salted password authentication."""

import hashlib
import secrets
from urllib.parse import urlsplit, urlunsplit

import requests

from .engine.config import REQUEST_TIMEOUT, required_env


def server_url(value):
    try:
        parts = urlsplit(str(value or "").strip())
        parts.port
        valid = (parts.scheme in {"http", "https"} and parts.hostname
                 and not parts.username and not parts.password and not parts.query and not parts.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Enter an http(s) server URL without credentials, a query, or a fragment.")
    path = parts.path.rstrip("/").removesuffix("/rest")
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


class Subsonic:
    def __init__(self, url=None, user=None, password=None, session=None):
        self.url = server_url(url or required_env("SUBSONIC_URL"))
        self.user = user or required_env("SUBSONIC_USER")
        self._password = password or required_env("SUBSONIC_PASSWORD")
        self._session = session or requests

    def call(self, method, **params):
        from .engine.targets.base import TargetAuthError, TargetCapabilityError, TargetTransientError

        salt = secrets.token_hex(12)
        token = hashlib.md5((self._password + salt).encode("utf-8")).hexdigest()
        query = {"u": self.user, "t": token, "s": salt, "v": "1.16.1",
                 "c": "SongMirror", "f": "json", **params}
        try:
            response = self._session.get(
                f"{self.url}/rest/{method}.view", params=query,
                headers={"User-Agent": "SongMirror"}, timeout=REQUEST_TIMEOUT,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise TargetTransientError(f"Could not reach the Subsonic server for {method}.") from exc
        # Do not include a response URL in errors: its query carries auth tokens.
        if response.status_code in {401, 403}:
            raise TargetAuthError("Subsonic rejected the account credentials or permissions.")
        if response.status_code == 429 or response.status_code >= 500:
            raise TargetTransientError(f"Subsonic returned HTTP {response.status_code} for {method}.")
        if not response.ok or 300 <= response.status_code < 400:
            raise RuntimeError(f"Subsonic returned HTTP {response.status_code}; check the server URL.")
        try:
            body = response.json()["subsonic-response"]
            if not isinstance(body, dict) or body.get("status") not in {"ok", "failed"}:
                raise ValueError
        except (ValueError, KeyError, TypeError) as exc:
            raise RuntimeError("The server did not return a Subsonic JSON response. Check the server URL.") from exc
        if body["status"] == "failed":
            code = (body.get("error") or {}).get("code")
            if code in {40, 41, 42, 43, 44}:
                raise TargetAuthError(f"Subsonic authentication failed (code {code}).")
            if code == 50:
                raise TargetCapabilityError("The Subsonic account is not allowed to perform this action.")
            raise RuntimeError(f"Subsonic rejected {method} (code {code}).")
        return body
