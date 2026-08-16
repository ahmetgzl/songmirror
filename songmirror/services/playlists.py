"""Playlist browsing + explicit cross-service pairing.

Browse reuses each provider's existing list_playlists; pairing lets the user link
differently-named playlists and set a per-pair direction, overriding the default
same-name matching. Services tier — drives the engine (build_one), never the web.
"""

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..engine import spotify, spotify_cookie
from ..engine.config import parse_args, spotify_write_backend
from ..engine.targets import build_one
from .settings import _open_private


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


class PlaylistService:
    def __init__(self, settings):
        self._settings = settings

    def browse(self, provider_id):
        """[{id, name, count, image, owned}] for one connected provider (empty if
        unconfigured). Provider-agnostic: every service is listed through its
        MirrorTarget.browse_playlists() + accessors, so adding a provider needs no
        change here. `owned` is False only for a followed (non-owned) playlist — a
        provider surfaces those by overriding browse_playlists (Spotify does today).
        Jellyfin is browse-only and lists via its own API."""
        self._settings.apply_to_env()
        if provider_id == "jellyfin":
            from ..engine import jellyfin
            rows = [{**r, "owned": True} for r in jellyfin.list_playlists()]
            return sorted(rows, key=lambda r: (r["name"] or "").casefold())
        opts = parse_args([])
        try:
            cookie = (provider_id == "spotify" and spotify_write_backend() == "cookie"
                      and spotify_cookie.configured())
            sp = spotify.client() if provider_id == "spotify" and not cookie else None
            target = build_one(provider_id, opts, sp)
        except Exception:
            return []  # e.g. Spotify not authorized yet -> nothing to browse
        if target is None:
            return []
        try:
            playlists = target.browse_playlists()
        except Exception:
            return []
        rows = [{"id": _pl_id(pl), "name": _pl_name(pl), "count": target.playlist_count(pl),
                 "image": _pl_image(pl), "owned": bool(pl.get("_owned", True))} for pl in playlists]
        return sorted(rows, key=lambda r: (r["name"] or "").casefold())


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
