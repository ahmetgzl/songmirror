"""Username-only public ListenBrainz connection."""

from ...listenbrainz import ListenBrainz
from .base import ConnStatus, Connector, Field


class ListenBrainzConnector(Connector):
    id = "listenbrainz"
    name = "ListenBrainz"
    auth_kind = "api_key"
    config_fields = [Field("LISTENBRAINZ_USER", "Username")]

    def status(self):
        user = self._store.get("LISTENBRAINZ_USER")
        if not user:
            return ConnStatus("unconfigured", capabilities=frozenset())
        try:
            ListenBrainz(user=user).feedback_page(count=1)
        except RuntimeError as exc:
            return ConnStatus("error", str(exc), capabilities=frozenset())
        return ConnStatus("connected", user, capabilities=frozenset({"library_read"}))

    def submit(self, values):
        self._store.save({"LISTENBRAINZ_USER": str(values.get("LISTENBRAINZ_USER") or "").strip()})
        return self.status()
