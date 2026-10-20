"""Public ListenBrainz loved recordings, including every feedback page."""

from datetime import datetime, timezone
from urllib.parse import quote

import requests

from .engine.config import REQUEST_TIMEOUT, polite_sleep, required_env

API = "https://api.listenbrainz.org"
PAGE_SIZE = 100


class ListenBrainz:
    def __init__(self, user=None, session=None):
        self.user = user or required_env("LISTENBRAINZ_USER")
        self._session = session or requests

    def feedback_page(self, offset=0, count=PAGE_SIZE):
        from .engine.targets.base import TargetTransientError

        try:
            response = self._session.get(
                f"{API}/1/feedback/user/{quote(self.user, safe='')}/get-feedback",
                params={"score": 1, "metadata": "true", "offset": offset, "count": count},
                headers={"User-Agent": "SongMirror"}, timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise TargetTransientError("Could not reach ListenBrainz; retry later.") from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise TargetTransientError(f"ListenBrainz returned HTTP {response.status_code}; retry later.")
        if response.status_code == 404:
            raise RuntimeError("ListenBrainz profile was not found. Check the username.")
        if not response.ok:
            raise RuntimeError(f"ListenBrainz returned HTTP {response.status_code}.")
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
            polite_sleep(0.25)


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
