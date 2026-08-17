"""Playlist browsing + explicit cross-service pairing.

Browse reuses each provider's existing list_playlists; pairing lets the user link
differently-named playlists and set a per-pair direction, overriding the default
same-name matching. Services tier — drives the engine (build_one), never the web.
"""

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote

from ..engine import archive, spotify, spotify_cookie
from ..engine.config import parse_args, spotify_write_backend
from ..engine.logs import log_warn
from ..engine.targets import build_one
from .settings import _open_private


_PROVIDER_NAMES = {
    "spotify": "Spotify",
    "tidal": "TIDAL",
    "qobuz": "Qobuz",
    "deezer": "Deezer",
    "amazon": "Amazon Music",
    "apple": "Apple Music",
    "ytmusic": "YouTube Music",
    "jellyfin": "Jellyfin",
}


class PlaylistServiceError(RuntimeError):
    status_code = 502


class PlaylistBrowseError(PlaylistServiceError):
    pass


class PlaylistNotFoundError(PlaylistServiceError):
    status_code = 404


class PlaylistReadOnlyError(PlaylistServiceError):
    status_code = 403


class PlaylistChangedError(PlaylistServiceError):
    status_code = 409


class _PlainTextParser(HTMLParser):
    """Extract provider-authored description text without rendering markup."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def _plain_text(value):
    parser = _PlainTextParser()
    parser.feed(str(value or ""))
    parser.close()
    return " ".join("".join(parser.parts).split())


# ponytail: provider playlist dicts store name/id differently (Spotify `name`,
# Apple `attributes.name`, YT `title`/`playlistId`). Read defensively here until
# Phase 3 adds playlist_name/playlist_id accessors to the MirrorTarget protocol.
def _pl_name(pl):
    return pl.get("name") or (pl.get("attributes") or {}).get("name") or pl.get("title") or ""


def _pl_id(pl):
    # The frontend/link-store contract uses string ids, but some providers
    # (notably Qobuz) return JSON numbers. Normalize at this shared boundary so
    # every consumer sees the same stable type.
    for key in ("id", "playlistId"):
        value = pl.get(key)
        if value is not None and value != "":
            return str(value)
    return _pl_name(pl)


def _pl_image(pl):
    """Best-effort cover-art URL across provider shapes (empty string if none)."""
    def entry_url(value):
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, dict):
            for key in ("url", "href", "src"):
                url = value.get(key)
                if isinstance(url, str) and url.strip():
                    return url.strip()
        return ""

    def first_url(values, *, reverse=False):
        if not isinstance(values, (list, tuple)):
            return entry_url(values)
        entries = reversed(values) if reverse else values
        for entry in entries:
            if url := entry_url(entry):
                return url
        return ""

    # Qobuz returns playlist and collage artwork as lists of URL strings.
    for key in ("image_rectangle", "images300", "image_rectangle_mini"):
        if url := first_url(pl.get(key)):
            return url

    # Deezer's Pipe API returns Picture objects, while its REST API returns
    # size-specific scalar fields. Picture.urls is ordered from small to large.
    for key in ("picture_xl", "picture_big", "picture_medium"):
        if url := entry_url(pl.get(key)):
            return url
    for key in ("picture", "defaultPicture"):
        picture = pl.get(key)
        if isinstance(picture, dict) and (url := first_url(picture.get("urls"), reverse=True)):
            return url
        if url := entry_url(picture):
            return url

    # Spotify, TIDAL, and Amazon use image objects; current Qobuz responses use
    # strings. Mixed/empty arrays are tolerated so one malformed card cannot
    # fail the entire provider browse response.
    if url := first_url(pl.get("images")):
        return url
    art = (pl.get("attributes") or {}).get("artwork") or {}  # Apple: {w}x{h} template
    if isinstance(art, dict) and art.get("url"):
        return art["url"].replace("{w}", "300").replace("{h}", "300")
    thumbs = pl.get("thumbnails") or (pl.get("snippet") or {}).get("thumbnails")  # YouTube
    if isinstance(thumbs, list) and thumbs:
        return first_url(thumbs, reverse=True)
    if isinstance(thumbs, dict):
        for size in ("high", "medium", "default"):
            if url := entry_url(thumbs.get(size)):
                return url
    return ""


def _external_url(provider_id, kind, item_id):
    """Stable first-party web URL for a provider playlist or track."""
    item_id = quote(str(item_id), safe="")
    routes = {
        "spotify": f"https://open.spotify.com/{kind}/{item_id}",
        "tidal": f"https://listen.tidal.com/{kind}/{item_id}",
        "qobuz": f"https://open.qobuz.com/{kind}/{item_id}",
        "deezer": f"https://www.deezer.com/{kind}/{item_id}",
        "amazon": f"https://music.amazon.com/{kind}s/{item_id}",
        "apple": (
            f"https://music.apple.com/library/playlist/{item_id}"
            if kind == "playlist"
            else f"https://music.apple.com/song/{item_id}"
        ),
        "ytmusic": (
            f"https://music.youtube.com/playlist?list={item_id}"
            if kind == "playlist"
            else f"https://music.youtube.com/watch?v={item_id}"
        ),
    }
    return routes.get(provider_id, "")


def _track_artist(track):
    if track.get("artist"):
        return str(track["artist"])
    artists = track.get("artists") or []
    names = []
    for artist in artists:
        name = artist.get("name") if isinstance(artist, dict) else artist
        if name:
            names.append(str(name))
    if names:
        return ", ".join(names)
    return str((track.get("attributes") or {}).get("artistName") or "")


class PlaylistService:
    def __init__(self, settings):
        self._settings = settings

    def _failure(self, provider_id, action, exc):
        label = _PROVIDER_NAMES.get(provider_id, provider_id)
        log_warn(f"{action} failed: {exc!r}", tag=provider_id)
        raise PlaylistBrowseError(
            f"{label} could not {action} right now. Retry; if it continues, reconnect the account."
        ) from exc

    def _target(self, provider_id):
        self._settings.apply_to_env()
        opts = parse_args([])
        try:
            cookie = (
                provider_id == "spotify"
                and spotify_write_backend() == "cookie"
                and spotify_cookie.configured()
            )
            sp = spotify.client() if provider_id == "spotify" and not cookie else None
            target = build_one(provider_id, opts, sp)
        except Exception as exc:
            self._failure(provider_id, "load playlists", exc)
        if target is None:
            label = _PROVIDER_NAMES.get(provider_id, provider_id)
            raise PlaylistBrowseError(
                f"{label} is not available. Connect or reconnect the account and retry."
            )
        return target

    def _with_cache(self, callback):
        """Run a short operation against the configured persistent song DB."""
        self._settings.apply_to_env()
        cache_file = os.getenv("SONG_CACHE_FILE") or str(
            self._settings.data_dir / "song_cache.db"
        )
        conn = archive.connect(cache_file)
        try:
            return callback(conn)
        finally:
            conn.close()

    def _cached_detail(self, provider_id, playlist_id):
        try:
            return self._with_cache(
                lambda conn: archive.get_playlist_detail_cache(
                    conn, provider_id, playlist_id
                )
            )
        except Exception as exc:
            log_warn(f"playlist cache read failed: {exc!r}", tag="cache")
            return None

    def _cache_detail(self, detail):
        try:
            self._with_cache(lambda conn: archive.set_playlist_detail_cache(conn, detail))
        except Exception as exc:
            log_warn(f"playlist cache write failed: {exc!r}", tag="cache")

    def _invalidate_detail(self, provider_id, playlist_id):
        try:
            self._with_cache(
                lambda conn: archive.invalidate_playlist_detail_cache(
                    conn, provider_id, playlist_id
                )
            )
        except Exception as exc:
            log_warn(f"playlist cache invalidation failed: {exc!r}", tag="cache")

    def _prune_details(self, provider_id, playlist_ids):
        try:
            self._with_cache(
                lambda conn: archive.prune_playlist_detail_cache(
                    conn, provider_id, playlist_ids
                )
            )
        except Exception as exc:
            log_warn(f"playlist cache pruning failed: {exc!r}", tag="cache")

    def browse(self, provider_id):
        """[{id, name, count, image, owned}] for one connected provider (empty if
        unconfigured). Provider-agnostic: every service is listed through its
        MirrorTarget.browse_playlists() + accessors, so adding a provider needs no
        change here. `owned` is False only for a followed (non-owned) playlist — a
        provider surfaces those by overriding browse_playlists (Spotify does today).
        Jellyfin is browse-only and lists via its own API."""
        if provider_id == "jellyfin":
            from ..engine import jellyfin
            self._settings.apply_to_env()
            server = (os.getenv("JELLYFIN_URL") or "").rstrip("/")
            rows = [{
                **row,
                "owned": True,
                "external_url": (
                    f"{server}/web/#/details?id={quote(str(row['id']), safe='')}"
                    if server else ""
                ),
            } for row in jellyfin.list_playlists()]
            return sorted(rows, key=lambda r: (r["name"] or "").casefold())
        target = self._target(provider_id)
        try:
            playlists = list(target.browse_playlists())
            hydrate_counts = getattr(target, "hydrate_playlist_counts", None)
            if hydrate_counts:
                playlists = hydrate_counts(playlists) or playlists
        except Exception as exc:
            self._failure(provider_id, "load playlists", exc)
        rows = [{"id": _pl_id(pl), "name": _pl_name(pl), "count": target.playlist_count(pl),
                 "image": _pl_image(pl), "owned": bool(pl.get("_owned", True)),
                 "external_url": _external_url(provider_id, "playlist", _pl_id(pl))}
                for pl in playlists]
        self._prune_details(provider_id, [row["id"] for row in rows])
        return sorted(rows, key=lambda r: (r["name"] or "").casefold())

    def detail(self, provider_id, playlist_id, *, refresh=False, expected_count=None):
        cached = self._cached_detail(provider_id, playlist_id)
        if (
            cached is not None
            and not refresh
            and (expected_count is None or cached["count"] == expected_count)
        ):
            return cached
        if provider_id == "jellyfin":
            raise PlaylistReadOnlyError(
                "Jellyfin playlist tracks are managed in Jellyfin. Open the service to edit them."
            )
        target = self._target(provider_id)
        try:
            playlist = target.find_playlist(str(playlist_id))
            if playlist is None:
                self._invalidate_detail(provider_id, playlist_id)
                raise PlaylistNotFoundError(
                    f"That {_PROVIDER_NAMES.get(provider_id, provider_id)} playlist no longer exists. Refresh Browse."
                )
            tracks = target.playlist_tracks(playlist)
        except PlaylistServiceError:
            raise
        except Exception as exc:
            self._failure(provider_id, "open that playlist", exc)

        normalized = []
        occurrence_id_getter = getattr(target, "occurrence_id", lambda track: None)
        for position, track in enumerate(tracks):
            track_id = target.track_id(track)
            if track_id is None:
                continue
            normalized.append({
                "position": position,
                "id": str(track_id),
                "isrc": str(track.get("isrc") or ""),
                "occurrence_id": str(occurrence_id_getter(track) or ""),
                "name": str(track.get("name") or track.get("title") or "Unknown track"),
                "artist": _track_artist(track),
                "album": track.get("album") or track.get("albumName"),
                "duration_ms": track.get("duration_ms") or track.get("durationInMillis"),
                "image": str(track.get("image") or _pl_image(track) or ""),
                "added_at": str(
                    track.get("added_at")
                    or track.get("addedAt")
                    or (track.get("attributes") or {}).get("dateAdded")
                    or ""
                ),
                "external_url": _external_url(provider_id, "track", track_id),
            })

        playlist_id_getter = getattr(target, "playlist_id", _pl_id)
        playlist_id = str(playlist_id_getter(playlist) or playlist_id)
        detail = {
            "provider": provider_id,
            "id": playlist_id,
            "name": target.playlist_name(playlist),
            "description": _plain_text(target.playlist_description(playlist)),
            "count": len(normalized),
            "image": _pl_image(playlist),
            "owned": bool(playlist.get("_owned", True)),
            "editable": bool(target.is_editable(playlist)),
            "external_url": _external_url(provider_id, "playlist", playlist_id),
            "tracks": normalized,
        }
        self._cache_detail(detail)
        return detail

    def remove_track(self, provider_id, playlist_id, *, position, track_id, occurrence_id=""):
        self.remove_tracks(provider_id, playlist_id, selections=[{
            "position": position,
            "track_id": track_id,
            "occurrence_id": occurrence_id,
        }])
        return {"ok": True}

    def remove_tracks(self, provider_id, playlist_id, *, selections):
        """Remove one or more explicitly selected physical playlist entries.

        Providers with durable occurrence ids can address each selected entry
        directly. Position-only providers are reread once and every selection
        is validated before the first mutation, so drift never turns a range
        selection into deletion of unrelated tracks.
        """
        target = self._target(provider_id)
        try:
            playlist = target.find_playlist(str(playlist_id))
            if playlist is None:
                raise PlaylistNotFoundError(
                    "That playlist no longer exists. Refresh Browse."
                )
            if not target.is_editable(playlist):
                raise PlaylistReadOnlyError(
                    "This playlist is read-only on the provider and cannot be edited here."
                )
            stable = bool(getattr(target, "stable_occurrence_ids", False))
            if stable and all(selection.get("occurrence_id") for selection in selections):
                # Invalidate before the first provider write: a later transient
                # failure can leave a valid partial result that must be reread.
                self._invalidate_detail(provider_id, playlist_id)
                for selection in selections:
                    target.remove_occurrence(
                        playlist,
                        str(selection["track_id"]),
                        str(selection["occurrence_id"]),
                    )
                return {"ok": True, "removed": len(selections)}

            tracks = target.playlist_tracks(playlist)
            positioned = []
            for selection in selections:
                position = int(selection["position"])
                if position < 0 or position >= len(tracks):
                    raise PlaylistChangedError(
                        "The playlist changed since it was opened. Refresh it before editing."
                    )
                track = tracks[position]
                if str(target.track_id(track)) != str(selection["track_id"]):
                    raise PlaylistChangedError(
                        "The playlist changed since it was opened. Refresh it before editing."
                    )
                positioned.append((position, track))
            self._invalidate_detail(provider_id, playlist_id)
            target.remove_occurrences(playlist, positioned)
            return {"ok": True, "removed": len(positioned)}
        except PlaylistServiceError:
            raise
        except Exception as exc:
            self._failure(provider_id, "remove the selected tracks", exc)


@dataclass
class PlaylistLink:
    name: str
    members: dict = field(default_factory=dict)  # provider_id -> playlist_id | None (None = create by name)
    direction: str = "oneway"                     # oneway | nway
    source: str | None = "spotify"
    enabled: bool = True
    id: str = ""


class LinkStore:
    """Explicit pairings persisted to data/links.json (owner-only, alongside the
    other data-dir state)."""

    def __init__(self, dir="data"):
        self._path = Path(dir) / "links.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def list(self):
        try:
            with open(self._path, encoding="utf-8") as f:
                return [PlaylistLink(**d) for d in json.load(f)]
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def upsert(self, link):
        if not link.id:
            link.id = uuid.uuid4().hex[:8]
        links = [l for l in self.list() if l.id != link.id]
        links.append(link)
        self._save(links)
        return link

    def delete(self, link_id):
        self._save([l for l in self.list() if l.id != link_id])

    def _save(self, links):
        with _open_private(self._path) as f:
            json.dump([asdict(l) for l in links], f, indent=2)
