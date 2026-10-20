"""Connect Navidrome and other Subsonic-compatible music servers."""

from ...subsonic import Subsonic, server_url
from .base import ConnStatus, Connector, Field


class SubsonicConnector(Connector):
    id = "subsonic"
    name = "Navidrome / Subsonic"
    auth_kind = "api_key"
    config_fields = [
        Field("SUBSONIC_URL", "Server URL"),
        Field("SUBSONIC_USER", "Username"),
        Field("SUBSONIC_PASSWORD", "Password", secret=True),
    ]

    def normalize_config(self, values):
        cleaned = dict(values)
        if cleaned.get("SUBSONIC_URL"):
            cleaned["SUBSONIC_URL"] = server_url(cleaned["SUBSONIC_URL"])
        if "SUBSONIC_USER" in cleaned:
            cleaned["SUBSONIC_USER"] = str(cleaned["SUBSONIC_USER"] or "").strip()
        return cleaned

    def status(self):
        if not self._configured("SUBSONIC_URL", "SUBSONIC_USER", "SUBSONIC_PASSWORD"):
            return ConnStatus("unconfigured")
        try:
            api = Subsonic(url=self._store.get("SUBSONIC_URL"), user=self._store.get("SUBSONIC_USER"),
                           password=self._store.get("SUBSONIC_PASSWORD"))
            info = api.call("ping")
        except (RuntimeError, ValueError) as exc:
            return ConnStatus("error", str(exc), capabilities=frozenset())
        label = info.get("type") or "Subsonic"
        return ConnStatus("connected", f"{self._store.get('SUBSONIC_USER')} · {label}")

    def submit(self, values):
        try:
            values = self.normalize_config(values)
        except ValueError as exc:
            return ConnStatus("error", str(exc), capabilities=frozenset())
        self._store.save({field.key: values[field.key] for field in self.config_fields if field.key in values})
        return self.status()
