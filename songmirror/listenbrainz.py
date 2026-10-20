"""Public ListenBrainz loved recordings, including every feedback page."""

import math
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import requests

from .engine.config import REQUEST_TIMEOUT, required_env

API = "https://api.listenbrainz.org"
WEBSITE = "https://listenbrainz.org"
PAGE_SIZE = 100
USER_AGENT = "SongMirror/0.1.0 (https://github.com/ahnafnafee/songmirror/issues)"


def _retry_delay(headers):
    delays = []
    for key in ("Retry-After", "X-RateLimit-Reset-In"):
        value = headers.get(key)
        try:
            delay = float(value)
        except (TypeError, ValueError):
            try:
                delay = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                continue
        if math.isfinite(delay) and delay > 0:
            delays.append(delay)
    return max(delays, default=30)


class _RequestGate:
    """Share the API's one-request-per-second budget across account profiles."""

    def __init__(self, *, clock=None, sleep=None):
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._lock = threading.Lock()
        self._next = self._retry_at = 0

    def request(self, send, url, **kwargs):
        from .engine.targets.base import TargetTransientError

        with self._lock:
            now = self._clock()
            if self._retry_at > now:
                delay = math.ceil(self._retry_at - now)
                raise TargetTransientError(
                    f"ListenBrainz rate limit reached; retry in {delay} seconds.", retry_after=delay,
                )
            if self._next > now:
                self._sleep(self._next - now)
            self._next = self._clock() + 1
            response = send(url, **kwargs)
            headers = getattr(response, "headers", {})
            if response.status_code == 429 or str(headers.get("X-RateLimit-Remaining")) == "0":
                delay = _retry_delay(headers)
                self._retry_at = self._clock() + delay
                if response.status_code == 429:
                    raise TargetTransientError(
                        f"ListenBrainz rate limit reached; retry in {math.ceil(delay)} seconds.",
                        retry_after=delay,
                    )
            return response


_REQUESTS = _RequestGate()


class ListenBrainz:
    def __init__(self, user=None, session=None):
        self.user = user or required_env("LISTENBRAINZ_USER")
        self._session = session or requests

    def feedback_page(self, offset=0, count=PAGE_SIZE):
        from .engine.targets.base import TargetTransientError

        try:
            response = _REQUESTS.request(
                self._session.get,
                f"{API}/1/feedback/user/{quote(self.user, safe='')}/get-feedback",
                params={"score": 1, "metadata": "true", "offset": offset, "count": count},
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise TargetTransientError("Could not reach ListenBrainz; retry later.") from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise TargetTransientError(f"ListenBrainz returned HTTP {response.status_code}; retry later.")
        if response.status_code == 404:
            raise RuntimeError("ListenBrainz profile was not found. Check the username.")
        if not response.ok:
            raise RuntimeError(f"ListenBrainz returned HTTP {response.status_code}.")
        content_type = getattr(response, "headers", {}).get("Content-Type", "")
        if "text/html" in content_type.lower():
            return self._website_feedback(offset, count)
        try:
            body = response.json()
            if not isinstance(body, dict) or not isinstance(body.get("feedback"), list):
                raise ValueError
            total = int(body["total_count"])
            if total < 0 or int(body["offset"]) != offset:
                raise ValueError
            if not all(isinstance(row, dict) for row in body["feedback"]):
                raise ValueError
        except (ValueError, KeyError, TypeError) as exc:
            raise RuntimeError("ListenBrainz returned incomplete feedback; sync was stopped.") from exc
        return body["feedback"], total

    def _website_feedback(self, offset, count):
        """Read the public Taste page's JSON, only when it contains all loves.

        This website route always returns its first 25 entries, regardless of
        pagination arguments. Its count must prove completeness before any
        slice can stand in for an API page.
        """
        from .engine.targets.base import TargetTransientError

        try:
            response = _REQUESTS.request(
                self._session.post,
                f"{WEBSITE}/user/{quote(self.user, safe='')}/taste/",
                params={"score": 1},
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=REQUEST_TIMEOUT, allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise TargetTransientError("Could not reach the ListenBrainz website; retry later.") from exc
        if response.status_code == 404:
            raise RuntimeError("ListenBrainz profile was not found. Check the username.")
        content_type = getattr(response, "headers", {}).get("Content-Type", "")
        if not response.ok or 300 <= response.status_code < 400 or "text/html" in content_type.lower():
            raise TargetTransientError(
                "ListenBrainz returned a web page or error instead of recording data. Try again later."
            )
        try:
            body = response.json()
            rows, total = body["feedback"], int(body["feedback_count"])
            user = body["user"]["name"]
            if (not isinstance(rows, list) or total < 0 or not isinstance(user, str)
                    or user.casefold() != self.user.casefold()
                    or not all(isinstance(row, dict) and row.get("score") == 1 for row in rows)):
                raise ValueError
        except (ValueError, KeyError, TypeError) as exc:
            raise TargetTransientError("ListenBrainz returned incomplete website data; sync was stopped.") from exc
        if len(rows) != total:
            raise TargetTransientError(
                f"ListenBrainz's website returned {len(rows)} of {total} loved recordings. "
                "The API is unavailable, so sync was stopped to avoid using a partial collection. Try again later."
            )
        return rows[offset:offset + count], total

    def loved_tracks(self):
        offset, tracks, seen, expected_total = 0, [], set(), None
        while True:
            rows, total = self.feedback_page(offset)
            if expected_total is not None and total != expected_total:
                raise RuntimeError("ListenBrainz loves changed during pagination; retry the sync.")
            expected_total = total
            if offset + len(rows) > total:
                raise RuntimeError("ListenBrainz returned an inconsistent feedback count.")
            if not rows and offset < total:
                raise RuntimeError("ListenBrainz feedback ended before all loved recordings were read.")
            for row in rows:
                if row.get("score") != 1:
                    raise RuntimeError("ListenBrainz returned feedback outside the loved collection.")
                track = _track(row)
                if track["id"] not in seen:
                    seen.add(track["id"])
                    tracks.append(track)
            offset += len(rows)
            if offset >= total:
                return tracks


def _track(row):
    metadata = row.get("track_metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    mbid, msid = row.get("recording_mbid"), row.get("recording_msid")
    if not (mbid or msid):
        raise RuntimeError("ListenBrainz returned a loved recording without an MBID or MSID.")
    try:
        added_at = datetime.fromtimestamp(float(row["created"]), timezone.utc).isoformat()
    except (KeyError, ValueError, TypeError, OverflowError, OSError):
        added_at = ""
    return {
        "id": f"mbid:{mbid}" if mbid else f"msid:{msid}",
        "recording_mbid": mbid, "recording_msid": msid,
        "name": str(metadata.get("track_name") or "").strip(),
        "artist": str(metadata.get("artist_name") or "").strip(),
        "album": str(metadata.get("release_name") or "").strip(),
        "duration_ms": None, "added_at": added_at,
        "_metadata_incomplete": not str(metadata.get("track_name") or "").strip()
                                or not str(metadata.get("artist_name") or "").strip(),
    }
