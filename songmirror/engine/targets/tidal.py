"""TIDAL playlist peer through the public JSON:API v2.

Only catalog metadata and the authenticated user's playlists are used here;
playback/download endpoints are intentionally outside this adapter.
"""

import os
import random
import re
import time
import uuid

import requests

from ...browser_session import jwt_expiry
from ...oauth import merge_refresh, read_token, token_is_live, token_path, write_token
from ...tidal_web import parse_web_headers
from ..config import REQUEST_TIMEOUT, polite_sleep, required_env
from ..matching import normalize_text, romanized, track_key
from .base import MirrorTarget, TargetAuthError
from .provider_utils import best_candidate, chunks, iso_duration_ms, source_playlist_details

API = "https://openapi.tidal.com/v2"
TOKEN_URL = "https://auth.tidal.com/v1/oauth2/token"
DEFAULT_TOKEN_FILE = "data/tidal_oauth.json"
MAX_ISRC_FILTER_VALUES = 20


class TidalTarget(MirrorTarget):
    name = "TIDAL"
    tag = "tidal"
    source = "tidal"
    stable_occurrence_ids = True

    def __init__(self):
        self.cache_file = os.getenv("TIDAL_CACHE_FILE", "tidal_resolve_cache.json")
        web_headers = (os.getenv("TIDAL_WEB_HEADERS") or "").strip()
        self._browser_mode = bool(web_headers)
        self._token_file = token_path("TIDAL_TOKEN_FILE", DEFAULT_TOKEN_FILE)
        if web_headers:
            try:
                context = parse_web_headers(web_headers)
            except ValueError as exc:
                raise RuntimeError(f"Invalid TIDAL_WEB_HEADERS: {exc}") from exc
            access_token = context["authorization"].split(None, 1)[1]
            self.country = context["country_code"]
            self._client_id = None
            self._tok = {"access_token": access_token}
            expiry = jwt_expiry(access_token)
            if expiry is not None:
                self._tok["expires_at"] = expiry
        else:
            configured_country = (os.getenv("TIDAL_COUNTRY_CODE") or "US").strip().upper()
            # Older wizard versions displayed country immediately below the
            # client id, so a client secret could be pasted there by mistake.
            # Never send arbitrary token-like data as a URL query parameter.
            self.country = configured_country if re.fullmatch(r"[A-Z]{2}", configured_country) else "US"
            self._client_id = required_env("TIDAL_CLIENT_ID")
            self._tok = read_token(self._token_file)
        if not self._tok.get("access_token") and not self._tok.get("refresh_token"):
            raise RuntimeError("Missing TIDAL OAuth token; connect TIDAL in Accounts")
        self._session = requests.Session()

    # -- auth / HTTP ---------------------------------------------------------
    def _access(self, force=False):
        if not force and token_is_live(self._tok):
            return self._tok["access_token"]
        refresh = self._tok.get("refresh_token")
        if not refresh:
            raise TargetAuthError(
                "TIDAL web-player authorization expired; paste a fresh OpenAPI request in Accounts."
                if self._browser_mode
                else "TIDAL authorization expired; reconnect TIDAL in Accounts."
            )
        try:
            response = requests.post(
                TOKEN_URL,
                data={"grant_type": "refresh_token", "refresh_token": refresh, "client_id": self._client_id},
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise TargetAuthError(f"TIDAL token refresh failed ({exc!r}).") from exc
        if not response.ok:
            raise TargetAuthError(
                f"TIDAL authorization expired (refresh returned HTTP {response.status_code}); reconnect in Accounts."
            )
        self._tok = merge_refresh(self._tok, response.json())
        write_token(self._token_file, self._tok)
        return self._tok["access_token"]

    def _request(self, method, path, *, params=None, json_body=None):
        url = path if str(path).startswith("http") else f"{API}/{str(path).lstrip('/')}"
        attempts = 5
        refreshed = False
        idempotency_key = str(uuid.uuid4()) if method != "GET" else None
        for attempt in range(attempts):
            headers = {
                "Authorization": f"Bearer {self._access()}",
                "Accept": "application/vnd.api+json",
                "Content-Type": "application/vnd.api+json",
            }
            if idempotency_key:
                headers["Idempotency-Key"] = idempotency_key
            try:
                response = self._session.request(
                    method, url, params=params, json=json_body, headers=headers, timeout=REQUEST_TIMEOUT
                )
            except requests.RequestException:
                if method == "GET" and attempt < attempts - 1:
                    time.sleep(min(2**attempt, 20) + random.uniform(0, 1.5))
                    continue
                raise
            if response.status_code == 401 and not refreshed:
                self._access(force=True)
                refreshed = True
                continue
            if response.status_code in (401, 403):
                raise TargetAuthError(
                    f"TIDAL refused {method} {url.removeprefix(API + '/')} ({response.status_code}); "
                    + (
                        "paste a fresh signed-in OpenAPI request in Accounts."
                        if self._browser_mode
                        else "reconnect and make sure playlists.read/playlists.write are enabled for the app."
                    )
                )
            if response.status_code == 429 and attempt < attempts - 1:
                wait = float(response.headers.get("Retry-After") or min(2**attempt, 15)) + random.uniform(0.5, 2)
                time.sleep(wait)
                continue
            if response.status_code >= 500 and method == "GET" and attempt < attempts - 1:
                time.sleep(min(2**attempt, 20) + random.uniform(0, 1.5))
                continue
            response.raise_for_status()
            return response
        raise RuntimeError("TIDAL request retry budget exhausted")

    def _pages(self, path, params=None):
        next_url, next_params = path, dict(params or {})
        while next_url:
            body = self._request("GET", next_url, params=next_params).json()
            yield body
            link = (body.get("links") or {}).get("next")
            next_url = link.get("href") if isinstance(link, dict) else link
            next_params = None

    # -- JSON:API normalization ---------------------------------------------
    @staticmethod
    def _resource_map(body):
        resources = {}
        for resource in body.get("included") or []:
            resources[(resource.get("type"), str(resource.get("id")))] = resource
        for resource in body.get("data") or []:
            if isinstance(resource, dict) and resource.get("attributes"):
                resources[(resource.get("type"), str(resource.get("id")))] = resource
        return resources

    @classmethod
    def _tracks_from_body(cls, body):
        resources = cls._resource_map(body)
        identifiers = body.get("data") or []
        tracks = []
        for identifier in identifiers:
            if identifier.get("type") != "tracks":
                continue
            resource = resources.get(("tracks", str(identifier.get("id"))), identifier)
            attrs = resource.get("attributes") or {}
            relationships = resource.get("relationships") or {}
            artist_ids = [str(a.get("id")) for a in ((relationships.get("artists") or {}).get("data") or [])]
            artists = [
                (resources.get(("artists", aid), {}).get("attributes") or {}).get("name", "")
                for aid in artist_ids
            ]
            artists = [a for a in artists if a]
            album_ids = [str(a.get("id")) for a in ((relationships.get("albums") or {}).get("data") or [])]
            album = ""
            image = ""
            if album_ids:
                album_resource = resources.get(("albums", album_ids[0]), {})
                album = (album_resource.get("attributes") or {}).get("title", "")
                cover_ids = [
                    str(item.get("id"))
                    for item in (((album_resource.get("relationships") or {}).get("coverArt") or {}).get("data") or [])
                    if item.get("id") is not None
                ]
                files = [
                    file
                    for cover_id in cover_ids
                    for file in ((resources.get(("artworks", cover_id), {}).get("attributes") or {}).get("files") or [])
                    if file.get("href")
                ]
                if files:
                    image = min(
                        files,
                        key=lambda file: abs(int((file.get("meta") or {}).get("width") or 160) - 160),
                    )["href"]
            meta = identifier.get("meta") or {}
            tracks.append(
                {
                    "id": str(resource.get("id") or identifier.get("id")),
                    "relationship_id": meta.get("itemId"),
                    "name": attrs.get("title", ""),
                    "artist": ", ".join(artists),
                    "artists": artists or [""],
                    "album": album or None,
                    "image": image,
                    "duration_ms": iso_duration_ms(attrs.get("duration")),
                    "isrc": attrs.get("isrc"),
                    "added_at": meta.get("addedAt") or "",
                }
            )
        return tracks

    # -- MirrorTarget --------------------------------------------------------
    def list_playlists(self):
        out = {}
        params = {"filter[owners.id]": "me", "countryCode": self.country, "include": ["coverArt"]}
        for body in self._pages("playlists", params):
            artworks = {
                str(resource.get("id")): resource
                for resource in body.get("included") or []
                if resource.get("type") == "artworks" and resource.get("id") is not None
            }
            for playlist in body.get("data") or []:
                attrs = playlist.get("attributes") or {}
                key = (attrs.get("name") or "").strip().casefold()
                if key and key not in out:
                    references = (
                        (((playlist.get("relationships") or {}).get("coverArt") or {}).get("data")) or []
                    )
                    image_urls = []
                    for reference in references:
                        artwork = artworks.get(str(reference.get("id"))) or {}
                        files = (artwork.get("attributes") or {}).get("files") or []
                        candidates = [
                            file
                            for file in files
                            if isinstance(file, dict) and isinstance(file.get("href"), str)
                        ]
                        if candidates:
                            best = min(
                                candidates,
                                key=lambda file: abs(int((file.get("meta") or {}).get("width") or 0) - 320),
                            )
                            image_urls.append({"url": best["href"]})
                    if image_urls:
                        playlist = {**playlist, "images": image_urls}
                    out[key] = playlist
        return out

    def create(self, source_playlist):
        name, description = source_playlist_details(source_playlist)
        attributes = {"name": name, "accessType": "UNLISTED"}
        if description:
            attributes["description"] = description
        body = {"data": {"type": "playlists", "attributes": attributes}}
        playlist = self._request("POST", "playlists", json_body=body).json()["data"]
        polite_sleep(0.4)
        return playlist

    def playlist_tracks(self, playlist):
        params = {"countryCode": self.country, "sort": "itemIndex"}
        tracks = []
        for body in self._pages(f"playlists/{playlist['id']}/relationships/items", params):
            identifiers = [item for item in body.get("data") or [] if item.get("type") == "tracks"]
            requested_ids = [str(item["id"]) for item in identifiers]
            details = self._tracks_by_id(requested_ids)
            missing = [track_id for track_id in requested_ids if track_id not in details]
            if missing:
                preview = ", ".join(missing[:5])
                suffix = "..." if len(missing) > 5 else ""
                raise RuntimeError(
                    "TIDAL playlist read incomplete: missing catalog details for "
                    f"{len(missing)} track relationship(s): {preview}{suffix}"
                )
            for item in identifiers:
                track = details[str(item["id"])]
                meta = item.get("meta") or {}
                tracks.append({**track, "relationship_id": meta.get("itemId"),
                               "added_at": meta.get("addedAt") or ""})
        return tracks

    def _tracks_by_id(self, ids):
        out = {}
        for group in chunks(list(dict.fromkeys(ids)), 25):
            if not group:
                continue
            body = self._request(
                "GET",
                "tracks",
                params={
                    "filter[id]": group,
                    "include": ["artists", "albums", "albums.coverArt"],
                    "countryCode": self.country,
                },
            ).json()
            for track in self._tracks_from_body(body):
                out[str(track["id"])] = track
        return out

    def track_id(self, track):
        return str(track.get("id")) if track.get("id") is not None else None

    def playlist_count(self, playlist):
        return (playlist.get("attributes") or {}).get("numberOfItems")

    def playlist_name(self, playlist):
        return (playlist.get("attributes") or {}).get("name", "")

    def playlist_description(self, playlist):
        return (playlist.get("attributes") or {}).get("description", "") or ""

    def prefetch(self, source_tracks, cache):
        isrcs = sorted({t.get("isrc") for t in source_tracks if t.get("isrc")})
        missing = [isrc for isrc in isrcs if isrc not in cache["isrc"]]
        for group in chunks(missing, MAX_ISRC_FILTER_VALUES):
            body = self._request(
                "GET",
                "tracks",
                params={"filter[isrc]": group, "include": ["artists", "albums"], "countryCode": self.country},
            ).json()
            found = {}
            for candidate in self._tracks_from_body(body):
                if candidate.get("isrc"):
                    found.setdefault(candidate["isrc"], []).append(candidate)
            for isrc in group:
                cache["isrc"][isrc] = found.get(isrc, [])
            cache["dirty"] = True
            polite_sleep(0.2)

    def native_isrc_map(self, cache):
        return {
            str(candidate["id"]): isrc
            for isrc, candidates in cache.get("isrc", {}).items()
            for candidate in candidates
            if candidate.get("id")
        }

    def expected_ids(self, source_tracks, links, cache):
        out = {}
        for track in source_tracks:
            ids = {str(c["id"]) for c in cache["isrc"].get(track.get("isrc") or "", []) if c.get("id")}
            if links.get(track.get("id")):
                ids.add(str(links[track["id"]]))
            if ids:
                out[track.get("id")] = ids
        return out

    def resolve(self, track, cache):
        candidates = cache["isrc"].get(track.get("isrc") or "", [])
        if candidates:
            return best_candidate(track, candidates) or str(candidates[0]["id"]), "isrc"
        key = track_key(track.get("name", ""), " ".join(track.get("artists") or []))
        if key in cache["search"]:
            return cache["search"][key], "search"
        primary = (track.get("artists") or [""])[0]
        terms = [f"{track.get('name', '')} {primary}".strip()]
        roman = f"{romanized(track.get('name'))} {romanized(primary)}".strip()
        if roman and roman != normalize_text(terms[0]):
            terms.append(roman)
        best = None
        for term in terms:
            body = self._request(
                "GET",
                "searchResults",
                params={
                    "filter[query]": term,
                    "include": ["tracks", "tracks.artists", "tracks.albums"],
                    "countryCode": self.country,
                },
            ).json()
            result = next(
                (item for item in body.get("data") or [] if item.get("type") == "searchResults"),
                {},
            )
            identifiers = ((result.get("relationships") or {}).get("tracks") or {}).get("data") or []
            candidates = self._tracks_from_body({"data": identifiers, "included": body.get("included") or []})
            best = best_candidate(track, candidates)
            if best:
                break
        cache["search"][key] = best
        cache["dirty"] = True
        polite_sleep(0.25)
        return best, "search"

    def add(self, playlist, target_ids):
        for target_id in target_ids:
            self._request(
                "POST",
                f"playlists/{playlist['id']}/relationships/items",
                json_body={"data": [{"type": "tracks", "id": str(target_id)}]},
            )
            polite_sleep(0.3)

    def remove(self, playlist, track):
        item_id = track.get("relationship_id")
        if not item_id:
            raise RuntimeError(f"TIDAL did not return the playlist entry id for track {track.get('id')}")
        self._request(
            "DELETE",
            f"playlists/{playlist['id']}/relationships/items",
            json_body={"data": [{"type": "tracks", "id": str(track["id"]), "meta": {"itemId": item_id}}]},
        )
        polite_sleep(0.3)
