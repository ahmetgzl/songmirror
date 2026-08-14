"""Amazon Music's first-party web-player GraphQL transport.

Amazon's documented Music Web API is a closed beta.  The consumer web player
uses a separate GraphQL endpoint, authenticated by short-lived request headers
from the signed-in browser.  The web player renews those headers through its
same-origin ``/pandaToken`` route.  This module persists only the minimal
GraphQL context plus a named allowlist of cookies needed by that renewal route;
analytics, experiment, AWS-console, and other unrelated cookies are discarded.

The endpoint is private/unsupported and can change without notice.  Keeping the
transport isolated here makes that failure mode explicit and easy to replace.
"""

from __future__ import annotations

import base64
import json
import random
import re
import time

import requests

from .engine.config import REQUEST_TIMEOUT
from .oauth import read_token, write_token

ENDPOINT = "https://gql.music.amazon.dev"
CONFIG_ENDPOINT = "https://music.amazon.com/config.json"
PANDA_TOKEN_ENDPOINT = "https://music.amazon.com/pandaToken"
DEFAULT_WEB_SESSION_FILE = "data/amazon_music_web_session.json"

# Public identifier embedded in Amazon Music's first-party Firefly web bundle.
# It identifies the web client; it is not a customer secret.  The signed-in
# ``config.json`` response supplies the per-user access token and device
# context used to construct the web player's Authorization value.
FIREFLY_WEB_API_KEY = "amzn1.application.e1dc16675f9f4c78b31927d5bfd5c229"

# The signed-in Firefly client only needs these two headers.  The optional
# context headers are accepted because older/newer web-player builds may emit
# anonymous-style requests while transitioning profiles.  Notably absent:
# Cookie, CSRF, Host, Origin, and every sec-* browser header.
_ALLOWED_HEADERS = {
    "authorization",
    "x-api-key",
    "device-id",
    "device-type",
    "x-device-id",
    "x-device-type",
    "music-territory",
    "x-amzn-session-id",
    "x-amzn-client-app-version",
    "accept-language",
}
_REQUIRED_HEADERS = {"authorization", "x-api-key"}

# Amazon's ``/pandaToken`` route is cookie-authenticated.  Keep only known
# authentication/session cookies observed on the Music request, never the
# complete amazon.com cookie jar.  These can still grant account access and
# therefore live only in SongMirror's owner-only settings/session files.
_ALLOWED_RENEWAL_COOKIES = {
    "am-token",
    "at-main",
    "at-main-music",
    "sess-at-main",
    "sid",
    "session-id",
    "session-id-time",
    "session-token",
    "ubid-main",
    "x-main",
}


class AmazonMusicWebAuthError(RuntimeError):
    """The pasted web-player session is missing, expired, or rejected."""


def _header_pairs(raw: str):
    """Yield header pairs from DevTools text, cURL, or stored JSON."""

    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        parsed = None
    if isinstance(parsed, dict):
        source = parsed.get("headers") if isinstance(parsed.get("headers"), dict) else parsed
        yield from source.items()
        return

    # Chrome/Firefox "Copy as cURL" output.
    for match in re.finditer(r"(?:^|\s)(?:-H|--header)\s+(?:'([^']*)'|\"([^\"]*)\")", raw):
        value = match.group(1) if match.group(1) is not None else match.group(2)
        if ":" in value:
            yield value.split(":", 1)

    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        # The raw Headers pane usually copies as ``name: value``.
        match = re.match(r"^([^:\s][^:]*):\s*(.+)$", line)
        if match:
            yield match.group(1), match.group(2)
            continue
        # Chromium sometimes copies the two visible table columns on separate
        # lines (``authorization`` then ``AmznMusic ...``).
        if line.casefold() in _ALLOWED_HEADERS and index + 1 < len(lines):
            yield line, lines[index + 1]


def parse_web_headers(raw: str) -> dict[str, str]:
    """Parse and minimize Amazon Music ``config.json`` or GraphQL headers.

    The returned dictionary is safe to persist in SongMirror's owner-only
    settings file.  It intentionally cannot contain an Amazon retail cookie.
    """

    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("paste the Response JSON from a signed-in Amazon Music config.json request")

    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        parsed = None
    if isinstance(parsed, dict) and any(
        key in parsed for key in ("accessToken", "deviceId", "deviceType", "musicTerritory")
    ):
        access_token = str(parsed.get("accessToken") or "").strip()
        device_id = str(parsed.get("deviceId") or "").strip()
        device_type = str(parsed.get("deviceType") or "").strip()
        if not access_token:
            raise ValueError("config.json has no accessToken; copy it after signing in to Amazon Music")
        if not device_id or not device_type:
            raise ValueError("config.json is missing deviceId or deviceType")
        if any("\r" in value or "\n" in value for value in (access_token, device_id, device_type)):
            raise ValueError("Amazon Music config values contain a line break")
        payload = json.dumps(
            {"deviceId": device_id, "deviceType": device_type, "access_token": access_token},
            separators=(",", ":"),
        ).encode()
        headers = {
            "authorization": "AmznMusic " + base64.b64encode(payload).decode(),
            "x-api-key": FIREFLY_WEB_API_KEY,
            "device-id": device_id,
            "device-type": device_type,
        }
        optional = {
            "music-territory": parsed.get("musicTerritory"),
            "x-amzn-session-id": parsed.get("sessionId"),
            "x-amzn-client-app-version": parsed.get("version"),
            "accept-language": parsed.get("locale") or parsed.get("language"),
        }
        for key, value in optional.items():
            if value not in (None, ""):
                normalized = str(value).strip()
                if "\r" in normalized or "\n" in normalized:
                    raise ValueError(f"{key} value contains a line break")
                headers[key] = normalized
        return headers

    headers: dict[str, str] = {}
    for name, value in _header_pairs(raw):
        key = str(name).strip().casefold()
        if key in _ALLOWED_HEADERS and value is not None:
            normalized = str(value).strip()
            if "\r" in normalized or "\n" in normalized:
                raise ValueError(f"{key} request header contains a line break")
            headers[key] = normalized

    missing = sorted(key for key in _REQUIRED_HEADERS if not headers.get(key))
    if missing:
        raise ValueError(
            "missing " + " and ".join(missing)
            + " request header(s); paste config.json Response JSON or GraphQL request headers"
        )
    if not headers["authorization"].casefold().startswith("amznmusic "):
        raise ValueError("authorization must come from a gql.music.amazon.dev web-player request")
    return headers


def serialize_web_headers(raw: str) -> str:
    """Return the whitelisted headers as compact, single-line JSON."""

    return json.dumps(parse_web_headers(raw), separators=(",", ":"), sort_keys=True)


def _add_cookie(out: dict[str, str], name, value) -> None:
    key = str(name).strip().casefold()
    if key not in _ALLOWED_RENEWAL_COOKIES or value is None:
        return
    normalized = str(value).strip().strip('"')
    if not normalized:
        return
    if any(char in normalized for char in "\r\n;"):
        raise ValueError(f"{key} cookie is malformed")
    out[key] = normalized


def _cookies_from_header(out: dict[str, str], value) -> None:
    for part in str(value or "").split(";"):
        name, separator, cookie_value = part.strip().partition("=")
        if separator:
            _add_cookie(out, name, cookie_value)


def parse_renewal_cookies(raw: str) -> dict[str, str]:
    """Extract the minimized cookie set used by Amazon Music ``/pandaToken``.

    Accepted inputs are copied request headers, Copy-as-cURL text, a bare
    Cookie value, or the compact JSON previously saved by SongMirror.
    """

    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(
            "paste a signed-in Amazon Music config.json or /pandaToken request (headers or cURL)"
        )

    out: dict[str, str] = {}
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        parsed = None
    if isinstance(parsed, dict):
        nested = parsed.get("renewal_cookies") or parsed.get("cookies")
        if isinstance(nested, dict):
            for name, value in nested.items():
                _add_cookie(out, name, value)
        for name, value in parsed.items():
            if str(name).strip().casefold() == "cookie":
                _cookies_from_header(out, value)
            else:
                _add_cookie(out, name, value)

    for name, value in _header_pairs(raw):
        key = str(name).strip().casefold()
        if key == "cookie":
            _cookies_from_header(out, value)
        else:
            _add_cookie(out, name, value)

    # Some Copy-as-cURL variants use ``-b``/``--cookie`` rather than a Cookie
    # header, and Firefox can copy only the Cookie header's bare value.
    for match in re.finditer(r"(?:^|\s)(?:-b|--cookie)\s+(?:'([^']*)'|\"([^\"]*)\")", raw):
        _cookies_from_header(out, match.group(1) if match.group(1) is not None else match.group(2))
    # Only apply the bare-value fallback when no structured request/header
    # parser found cookies.  Re-parsing a complete header block here made its
    # final cookie absorb every following request-header line.
    if not out:
        _cookies_from_header(out, raw)

    if not out:
        raise ValueError(
            "no supported Amazon Music authentication cookies found; copy a signed-in "
            "config.json or /pandaToken request's headers or cURL"
        )
    return out


def serialize_renewal_cookies(raw: str) -> str:
    return json.dumps(parse_renewal_cookies(raw), separators=(",", ":"), sort_keys=True)


SESSION_QUERY = """
query SongMirrorAmazonSession {
  user { id }
}
"""


class AmazonMusicWebClient:
    """GraphQL client backed by Amazon Music's renewable first-party session."""

    def __init__(
        self,
        raw_headers: str = "",
        *,
        renewal_request: str = "",
        token_file: str = "",
        prefer_persisted: bool = True,
        session=None,
        endpoint: str = ENDPOINT,
        config_endpoint: str = CONFIG_ENDPOINT,
        panda_token_endpoint: str = PANDA_TOKEN_ENDPOINT,
    ):
        self.endpoint = endpoint
        self.config_endpoint = config_endpoint
        self.panda_token_endpoint = panda_token_endpoint
        self.session = session or requests.Session()
        self._token_file = str(token_file or "")

        persisted = read_token(self._token_file) if self._token_file else {}
        supplied_headers = parse_web_headers(raw_headers) if str(raw_headers or "").strip() else {}
        stored_headers = persisted.get("headers") if isinstance(persisted.get("headers"), dict) else {}
        if stored_headers:
            stored_headers = parse_web_headers(json.dumps(stored_headers))
        if prefer_persisted and stored_headers:
            self.headers = stored_headers
            self._expires_at = self._number(persisted.get("expires_at"))
        else:
            self.headers = supplied_headers or stored_headers
            self._expires_at = 0

        supplied_cookies = (
            parse_renewal_cookies(renewal_request) if str(renewal_request or "").strip() else {}
        )
        stored_cookies = persisted.get("renewal_cookies")
        if isinstance(stored_cookies, dict) and stored_cookies:
            stored_cookies = parse_renewal_cookies(json.dumps(stored_cookies))
        else:
            stored_cookies = {}
        self.renewal_cookies = (
            stored_cookies
            if prefer_persisted and stored_cookies
            else supplied_cookies or stored_cookies
        )

        if not self.headers and not self.renewal_cookies:
            raise ValueError(
                "paste a signed-in Amazon Music config.json or /pandaToken request; the config "
                "response is an optional bootstrap"
            )

    @staticmethod
    def _number(value) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _response_json(response, label: str) -> dict:
        try:
            body = response.json()
        except ValueError as exc:
            raise AmazonMusicWebAuthError(f"Amazon Music {label} returned a non-JSON response.") from exc
        if not isinstance(body, dict):
            raise AmazonMusicWebAuthError(f"Amazon Music {label} returned an invalid response.")
        return body

    def _merge_response_cookies(self, response) -> None:
        jar = getattr(response, "cookies", None)
        if jar is None:
            return
        try:
            values = jar.get_dict()
        except AttributeError:
            try:
                values = dict(jar)
            except (TypeError, ValueError):
                return
        for name, value in values.items():
            _add_cookie(self.renewal_cookies, name, value)

    def _persist_session(self) -> None:
        if not self._token_file:
            return
        state = {
            "headers": self.headers,
            "renewal_cookies": self.renewal_cookies,
        }
        if self._expires_at:
            state["expires_at"] = self._expires_at
        write_token(self._token_file, state)

    @staticmethod
    def _authorization_context(headers: dict[str, str]) -> dict:
        authorization = str(headers.get("authorization") or "")
        if not authorization.casefold().startswith("amznmusic "):
            return {}
        try:
            return json.loads(base64.b64decode(authorization.split(None, 1)[1]))
        except (ValueError, TypeError, json.JSONDecodeError):
            return {}

    def _renew(self) -> None:
        if not self.renewal_cookies:
            raise AmazonMusicWebAuthError(
                "Amazon Music access token expired; reconnect once with a signed-in config.json or "
                "/pandaToken request "
                "to enable automatic renewal."
            )

        browser_headers = {
            "Accept": "application/json",
            "Origin": "https://music.amazon.com",
            "Referer": "https://music.amazon.com/",
        }
        try:
            config_response = self.session.get(
                self.config_endpoint,
                headers=browser_headers,
                cookies=self.renewal_cookies,
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise AmazonMusicWebAuthError(f"Amazon Music config renewal failed ({exc!r}).") from exc
        if config_response.status_code in (401, 403):
            raise AmazonMusicWebAuthError(
                "Amazon Music renewal session expired or was revoked; reconnect with a fresh "
                "config.json or /pandaToken request."
            )
        config_response.raise_for_status()
        config = self._response_json(config_response, "config renewal")
        self._merge_response_cookies(config_response)

        try:
            token_response = self.session.get(
                self.panda_token_endpoint,
                headers=browser_headers,
                cookies=self.renewal_cookies,
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise AmazonMusicWebAuthError(f"Amazon Music token renewal failed ({exc!r}).") from exc
        if token_response.status_code in (400, 401, 403):
            raise AmazonMusicWebAuthError(
                "Amazon Music renewal session expired or was revoked; reconnect with a fresh "
                "config.json or /pandaToken request."
            )
        token_response.raise_for_status()
        token = self._response_json(token_response, "token renewal")
        self._merge_response_cookies(token_response)

        access_token = str(token.get("accessToken") or config.get("accessToken") or "").strip()
        current = self._authorization_context(self.headers)
        device_id = str(
            config.get("deviceId") or self.headers.get("device-id") or current.get("deviceId") or ""
        ).strip()
        device_type = str(
            config.get("deviceType") or self.headers.get("device-type") or current.get("deviceType") or ""
        ).strip()
        if not access_token:
            raise AmazonMusicWebAuthError(
                "Amazon Music /pandaToken did not return an access token; reconnect after signing in."
            )
        if not device_id or not device_type:
            raise AmazonMusicWebAuthError(
                "Amazon Music config.json did not return device context; reload the signed-in web player "
                "and reconnect."
            )

        refreshed_config = dict(config)
        refreshed_config.update(
            {"accessToken": access_token, "deviceId": device_id, "deviceType": device_type}
        )
        self.headers = parse_web_headers(json.dumps(refreshed_config))
        expires_in = self._number(token.get("expiresIn") or token.get("expires_in"))
        self._expires_at = time.time() + expires_in if expires_in > 0 else 0
        self._persist_session()

    def _ensure_access(self) -> bool:
        if not self.headers or (self._expires_at and self._expires_at <= time.time() + 90):
            self._renew()
            return True
        return False

    def serialized_headers(self) -> str:
        return json.dumps(self.headers, separators=(",", ":"), sort_keys=True)

    def serialized_renewal(self) -> str:
        return json.dumps(self.renewal_cookies, separators=(",", ":"), sort_keys=True)

    @staticmethod
    def _auth_error(message: str) -> bool:
        lowered = message.casefold()
        return any(
            marker in lowered
            for marker in (
                "access denied",
                "auth_",
                "authorization",
                "forbidden",
                "invalid access token",
                "not authenticated",
                "not authorized",
                "session expired",
                "token expired",
                "token_expired",
                "unauthenticated",
                "unauthorized",
            )
        )

    def execute(self, operation_name: str, query: str, variables=None, *, mutation=False):
        """Execute one operation and refresh/retry once on auth rejection."""

        attempts = (2 if self.renewal_cookies else 1) if mutation else 5
        refreshed = self._ensure_access()
        for attempt in range(attempts):
            headers = {
                **self.headers,
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Origin": "https://music.amazon.com",
                "Referer": "https://music.amazon.com/",
            }
            try:
                response = self.session.post(
                    self.endpoint,
                    headers=headers,
                    json={
                        "operationName": operation_name,
                        "query": query,
                        "variables": variables or {},
                    },
                    timeout=REQUEST_TIMEOUT,
                )
            except requests.RequestException:
                if not mutation and attempt < attempts - 1:
                    time.sleep(min(2**attempt, 20) + random.uniform(0, 1.5))
                    continue
                raise

            if response.status_code in (401, 403):
                if self.renewal_cookies and not refreshed:
                    self._renew()
                    refreshed = True
                    continue
                raise AmazonMusicWebAuthError(
                    "Amazon Music renewal session expired or was rejected; reconnect with a fresh "
                    "config.json or /pandaToken request."
                )
            if not mutation and response.status_code == 429 and attempt < attempts - 1:
                time.sleep(float(response.headers.get("Retry-After") or 2**attempt) + random.uniform(0.5, 2))
                continue
            if not mutation and response.status_code >= 500 and attempt < attempts - 1:
                time.sleep(min(2**attempt, 20) + random.uniform(0, 1.5))
                continue
            response.raise_for_status()
            try:
                body = response.json()
            except ValueError as exc:
                raise RuntimeError("Amazon Music web API returned a non-JSON response") from exc

            errors = body.get("errors") if isinstance(body, dict) else None
            if errors:
                messages = "; ".join(str(error.get("message", error)) for error in errors)
                codes = " ".join(
                    str((error.get("extensions") or {}).get("code", "")) for error in errors
                )
                if self._auth_error(f"{messages} {codes}"):
                    if self.renewal_cookies and not refreshed:
                        self._renew()
                        refreshed = True
                        continue
                    raise AmazonMusicWebAuthError(
                        "Amazon Music renewal session expired or was rejected; reconnect with a fresh "
                        "config.json or /pandaToken request."
                    )
                raise RuntimeError(f"Amazon Music web API error: {messages}")
            return body.get("data") or {}
        raise RuntimeError("Amazon Music web request retry budget exhausted")

    def validate(self) -> None:
        data = self.execute("SongMirrorAmazonSession", SESSION_QUERY)
        if not (data.get("user") or {}).get("id"):
            raise AmazonMusicWebAuthError(
                "Amazon Music did not recognize a signed-in user; reconnect from a signed-in web player."
            )
