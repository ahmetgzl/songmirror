"""Jellyfin connector (api_key) — optional, for pushing playlist covers."""

from ...engine.jellyfin import JellyfinError, get_json, server_url

from .base import ConnStatus, Connector, Field


class JellyfinConnector(Connector):
    id = "jellyfin"
    name = "Jellyfin"
    auth_kind = "api_key"
    config_fields = [
        Field("JELLYFIN_URL", "Server URL", help="e.g. http://localhost:8096 (in Docker: http://host.docker.internal:8096)"),
        Field("JELLYFIN_API_KEY", "API key", secret=True, help="Jellyfin Dashboard → API Keys → New"),
        Field("JELLYFIN_USER_ID", "User ID", required=False, help="Optional; only if listing playlists needs it"),
    ]

    def status(self) -> ConnStatus:
        if not self._configured("JELLYFIN_URL", "JELLYFIN_API_KEY"):
            return ConnStatus("unconfigured")
        return ConnStatus("connected", self._store.get("JELLYFIN_URL"))

    def submit(self, values: dict) -> ConnStatus:
        try:
            values = self.normalize_config(values)
        except ValueError as exc:
            return ConnStatus("error", str(exc))
        self._store.save({k: values.get(k) for k in ("JELLYFIN_URL", "JELLYFIN_API_KEY", "JELLYFIN_USER_ID")})
        ok, detail = self._ping()
        return ConnStatus("connected" if ok else "error", detail)

    def normalize_config(self, values: dict) -> dict:
        cleaned = {key: str(value).strip() for key, value in values.items() if value is not None}
        if cleaned.get("JELLYFIN_URL"):
            try:
                cleaned["JELLYFIN_URL"] = server_url(cleaned["JELLYFIN_URL"])
            except JellyfinError as exc:
                raise ValueError(str(exc)) from exc
        return cleaned

    def _ping(self):
        url = self._store.get("JELLYFIN_URL")
        key = self._store.get("JELLYFIN_API_KEY")
        if not (url and key):
            return False, "missing url/key"
        try:
            info = get_json(server_url(url), key, "/System/Info", timeout=10)
            if not (info.get("Id") and info.get("Version")):
                raise JellyfinError("Jellyfin did not return server information. Check the server URL and reverse proxy.")
            return True, ""
        except JellyfinError as exc:
            return False, str(exc)
