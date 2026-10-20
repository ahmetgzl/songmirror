"""Navidrome/Subsonic playlists and starred songs, using the server's library."""

import os

from ...subsonic import Subsonic
from ..config import polite_sleep
from ..matching import normalize_isrc, recording_metadata_compatible, track_key
from .base import MirrorTarget, TargetCapabilityError
from .provider_utils import best_candidate, source_playlist_details


def _rows(envelope, key):
    if not isinstance(envelope, dict):
        raise RuntimeError("Subsonic returned an incomplete collection; sync was stopped.")
    rows = envelope.get(key, [])
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise RuntimeError("Subsonic returned an invalid collection; sync was stopped.")
    return rows


def _track(row, *, favorite=False):
    if not row.get("id") or row.get("isDir") or row.get("isVideo"):
        raise TargetCapabilityError("The Subsonic collection contains an unsupported or unidentified item.")
    artists = [artist["name"] for artist in row.get("artists") or []
               if isinstance(artist, dict) and artist.get("name")]
    artist = row.get("displayArtist") or row.get("artist") or ", ".join(artists)
    if not artists:
        artists = [artist] if artist else []
    isrcs = row.get("isrc") or []
    if isinstance(isrcs, str):
        isrcs = [isrcs]
    isrc = next((normalized for value in isrcs if (normalized := normalize_isrc(value))), None)
    duration = row.get("duration")
    try:
        duration_ms = round(float(duration) * 1000) if duration is not None else None
    except (ValueError, TypeError, OverflowError):
        duration_ms = None
    return {
        "id": str(row["id"]), "name": row.get("title") or "", "artist": artist,
        "artists": artists, "album": row.get("album") or "", "isrc": isrc,
        "duration_ms": duration_ms, "added_at": row.get("starred") or "" if favorite else "",
    }


class SubsonicTarget(MirrorTarget):
    name = "Navidrome / Subsonic"
    tag = "subsonic"
    source = "subsonic"
    catalog_ids_global = False
    favorite_tracks_name = "Starred Songs"

    def __init__(self, client=None):
        self._api = client or Subsonic()
        self.cache_file = self.resolve_cache_path()

    @classmethod
    def resolve_cache_path(cls, opts=None):
        return os.getenv("SUBSONIC_CACHE_FILE", "subsonic_resolve_cache.json")

    @classmethod
    def creates_public_playlists(cls):
        return True

    @classmethod
    def editable_details(cls):
        return frozenset({"name", "description", "public"})

    def _playlist(self, row):
        if not isinstance(row, dict) or not row.get("id"):
            raise RuntimeError("Subsonic returned an unidentified playlist; sync was stopped.")
        return {**row, "id": str(row["id"]), "description": row.get("comment") or "",
                "_owned": row.get("owner") == self._api.user}

    def browse_playlists(self):
        body = self._api.call("getPlaylists")
        return [self._playlist(row) for row in _rows(body.get("playlists"), "playlist")]

    def list_playlists(self):
        best = {}
        for playlist in self.browse_playlists():
            key = self.playlist_name(playlist).strip().casefold()
            if key not in best or (self.is_editable(playlist) and not self.is_editable(best[key])):
                best[key] = playlist
        return best

    def is_editable(self, playlist):
        return playlist.get("owner") == self._api.user

    def playlist_count(self, playlist):
        return playlist.get("songCount")

    def playlist_public(self, playlist):
        return playlist.get("public") if isinstance(playlist.get("public"), bool) else None

    def fetch_playlist(self, playlist_id):
        return self._playlist(self._api.call("getPlaylist", id=str(playlist_id)).get("playlist"))

    def playlist_tracks(self, playlist):
        raw = self._api.call("getPlaylist", id=playlist["id"]).get("playlist")
        self._playlist(raw)
        rows = _rows(raw, "entry")
        if raw.get("songCount") is None or int(raw["songCount"]) != len(rows):
            raise RuntimeError("Subsonic playlist read was incomplete; sync was stopped.")
        return [_track(row) for row in rows]

    def favorite_tracks(self):
        rows = _rows(self._api.call("getStarred2").get("starred2"), "song")
        return [_track(row, favorite=True) for row in rows]

    def track_id(self, track):
        return str(track["id"]) if track.get("id") is not None else None

    @classmethod
    def normalize_manual_track_id(cls, value):
        raw = super().normalize_manual_track_id(value)
        if "://" in raw or any(char.isspace() for char in raw):
            raise ValueError("Paste a song id from your Subsonic server.")
        return raw

    def create(self, sp_playlist):
        name, description = source_playlist_details(sp_playlist)
        public = self._create_public(sp_playlist)
        playlist = self._playlist(self._api.call("createPlaylist", name=name).get("playlist"))
        # Servers may default new playlists to public; explicitly set visibility.
        self.update_details(playlist, {"description": description, "public": public})
        return {**playlist, "description": description, "public": public}

    def update_details(self, playlist, changes):
        if not self.is_editable(playlist):
            raise TargetCapabilityError("Only the owner can edit this Subsonic playlist.")
        params = {"comment" if key == "description" else key:
                  str(value).lower() if isinstance(value, bool) else value
                  for key, value in changes.items()}
        self._api.call("updatePlaylist", playlistId=playlist["id"], **params)

    def search_candidates(self, query, *, limit=5):
        if not str(query or "").strip():
            return []
        body = self._api.call("search3", query=str(query).strip(), artistCount=0, albumCount=0,
                              songCount=max(1, limit), songOffset=0)
        return [_track(row) for row in _rows(body.get("searchResult3"), "song")]

    def resolve(self, track, cache):
        key = track_key(track.get("name") or "", " ".join(track.get("artists") or []))
        if key in cache["search"]:
            return cache["search"][key], "search"
        if not track.get("name"):
            return None, None
        primary = (track.get("artists") or [track.get("artist") or ""])[0]
        queries = list(dict.fromkeys([f"{track['name']} {primary}".strip(), track["name"]]))
        best, method = None, "search"
        for query in queries:
            candidates = self.search_candidates(query, limit=30)
            isrc = normalize_isrc(track.get("isrc"))
            exact = [candidate for candidate in candidates if isrc and candidate["isrc"] == isrc
                     and recording_metadata_compatible(track, candidate)]
            best = best_candidate(track, exact or candidates)
            if best:
                method = "isrc" if exact else "search"
                break
        cache["search"][key] = best
        cache["dirty"] = True
        return best, method

    def add(self, playlist, target_ids):
        if not self.is_editable(playlist):
            raise TargetCapabilityError("Only the owner can edit this Subsonic playlist.")
        for target_id in target_ids:
            self._api.call("updatePlaylist", playlistId=playlist["id"], songIdToAdd=str(target_id))
            polite_sleep(0.1)

    def remove(self, playlist, track):
        rows = self.playlist_tracks(playlist)
        position = next((i for i, row in enumerate(rows) if self.track_id(row) == self.track_id(track)), None)
        if position is not None:
            self.remove_occurrences(playlist, [(position, rows[position])])

    def remove_occurrences(self, playlist, positioned):
        if not self.is_editable(playlist):
            raise TargetCapabilityError("Only the owner can edit this Subsonic playlist.")
        positioned = list(positioned)
        current = self.playlist_tracks(playlist)
        if any(position < 0 or position >= len(current)
               or self.track_id(current[position]) != self.track_id(row) for position, row in positioned):
            raise RuntimeError("Subsonic playlist changed; refresh it before removing tracks.")
        # Descending batches preserve all remaining read-time positions.
        positions = sorted({position for position, _ in positioned}, reverse=True)
        for start in range(0, len(positions), 100):
            self._api.call("updatePlaylist", playlistId=playlist["id"],
                           songIndexToRemove=positions[start:start + 100])

    def add_favorite_tracks(self, target_ids):
        for target_id in target_ids:
            self._api.call("star", id=str(target_id))
            polite_sleep(0.1)

    def remove_favorite_track(self, track):
        self._api.call("unstar", id=self.track_id(track))
