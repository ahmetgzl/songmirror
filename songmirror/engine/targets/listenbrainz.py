"""ListenBrainz favorites: public reads and token-authorized love/unlove."""

import os

from ...listenbrainz import ListenBrainz
from ..logs import log_warn
from ..matching import score_candidate
from .base import MirrorTarget, TargetCapabilityError


class ListenBrainzTarget(MirrorTarget):
    name = "ListenBrainz"
    tag = "listenbrainz"
    source = "listenbrainz"
    supports_playlists = False
    favorite_tracks_name = "Loved Recordings"
    favorite_tracks_id = "loved"
    replay_chronology = None
    no_playlists_note = "ListenBrainz playlist sync is not supported; use its Loved Recordings collection."

    def __init__(self, client=None):
        self._api = client or ListenBrainz()
        self.cache_file = self.resolve_cache_path()

    @classmethod
    def resolve_cache_path(cls, opts=None):
        return os.getenv("LISTENBRAINZ_CACHE_FILE", "listenbrainz_resolve_cache.json")

    def list_playlists(self):
        return {}

    def is_editable(self, playlist):
        return False

    def validate_favorite_tracks(self, *, write=False, remove=False):
        if write or remove:
            self._api.validate_token()

    def favorite_tracks(self):
        tracks = self._api.loved_tracks()
        for track in tracks:
            if not track.get("name") or not track.get("artist"):
                log_warn(
                    f"Loved recording {track['id']} has incomplete metadata; "
                    "it remains unresolved and destination removals are held.", tag=self.tag,
                )
        return tracks

    def track_id(self, track):
        return track.get("id")

    def resolve(self, track, cache):
        name = track.get("name") or ""
        artists = track.get("artists") or ([track["artist"]] if track.get("artist") else [])
        artists = [artist for artist in artists if artist.strip()]
        key = self.search_cache_key(name, artists)
        if key in cache["search"]:
            return self._cached_id(key, cache), "search"
        raw = track.get("_raw") or track
        # Recording MBIDs carried by a source are already global identifiers.
        # MSIDs describe ListenBrainz submissions, not another provider's ids.
        for kind in ("mbid", "msid"):
            identifier = raw.get(f"recording_{kind}")
            if identifier:
                try:
                    found = self.normalize_manual_track_id(f"{kind}:{identifier}")
                except ValueError:
                    continue
                if name and artists:
                    cache["search"][key] = found
                    cache["dirty"] = True
                return found, kind
        if not name or not artists:
            return None, "search"
        body = self._api.lookup_recording(" ".join(artists), name, raw.get("album") or "")
        found = None
        if body.get("recording_mbid"):
            candidate_name, candidate_artist = body.get("recording_name"), body.get("artist_credit_name")
            if (not isinstance(candidate_name, str) or not candidate_name.strip()
                    or not isinstance(candidate_artist, str) or not candidate_artist.strip()):
                raise RuntimeError("ListenBrainz returned incomplete recording metadata; matching was stopped.")
            candidate_id = self.normalize_manual_track_id(f"mbid:{body['recording_mbid']}")
            _, acceptable = score_candidate(
                name, artists, track.get("duration_ms"), candidate_name, candidate_artist, None,
            )
            if acceptable:
                found = candidate_id
        cache["search"][key] = found
        cache["dirty"] = True
        return found, "search"

    def add_favorite_tracks(self, target_ids):
        # Reject malformed saved references before writing even the first item.
        ids = [self.normalize_manual_track_id(value) for value in target_ids]
        if any(not value for value in ids):
            raise ValueError("A ListenBrainz recording identifier is required.")
        for target_id in dict.fromkeys(ids):
            self._api.set_feedback(target_id, 1)

    def remove_favorite_track(self, track):
        self._api.set_feedback(self.track_id(track), 0)

    def _no_playlists(self, *args, **kwargs):
        raise TargetCapabilityError(self.no_playlists_note)

    create = _no_playlists
    playlist_tracks = _no_playlists
    add = _no_playlists
    remove = _no_playlists
