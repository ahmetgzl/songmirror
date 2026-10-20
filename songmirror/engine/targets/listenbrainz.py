"""ListenBrainz supplies loved recordings through public, read-only feedback."""

import os

from ...listenbrainz import ListenBrainz
from ..logs import log_warn
from .base import MirrorTarget, TargetCapabilityError


class ListenBrainzTarget(MirrorTarget):
    name = "ListenBrainz"
    tag = "listenbrainz"
    source = "listenbrainz"
    supports_playlists = False
    supports_favorite_writes = False
    favorite_tracks_name = "Loved Recordings"
    favorite_tracks_id = "loved"
    replay_chronology = None
    no_playlists_note = "ListenBrainz is a read-only loved-recordings source."

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
            raise TargetCapabilityError(self.no_playlists_note)

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

    def _read_only(self, *args, **kwargs):
        raise TargetCapabilityError(self.no_playlists_note)

    create = _read_only
    playlist_tracks = _read_only
    resolve = _read_only
    add = _read_only
    remove = _read_only
    add_favorite_tracks = _read_only
    remove_favorite_track = _read_only
