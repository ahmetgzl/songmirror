"""Web layer smoke tests (FastAPI TestClient)."""

from fastapi.testclient import TestClient

from songmirror.services.settings import SettingsStore
from songmirror.services.syncs import SyncStore
from songmirror.web import create_app


def _app(tmp_path):
    return create_app(settings=SettingsStore(dir=tmp_path))


def test_health(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        assert client.get("/health").json() == {"ok": True}


def test_accounts_list_all_unconfigured(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        accounts = client.get("/api/accounts").json()
        assert {a["id"] for a in accounts} == {
            "spotify", "tidal", "qobuz", "deezer", "amazon", "apple", "ytmusic", "jellyfin"
        }
        assert all(a["state"] == "unconfigured" for a in accounts)


def test_settings_roundtrip_masks_secrets(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        client.put("/api/settings", json={"SYNC_INTERVAL": "30m", "SPOTIFY_CLIENT_SECRET": "shh"})
        got = client.get("/api/settings").json()
        assert got["SYNC_INTERVAL"] == "30m"
        assert "SPOTIFY_CLIENT_SECRET" not in got  # secret never echoed back


def test_settings_falls_back_to_env(tmp_path, monkeypatch):
    # A key absent from settings.json is filled from the process env (a docker
    # env_file / .env), so the UI shows the actual running config.
    monkeypatch.setenv("MAX_ADDS", "321")
    with TestClient(_app(tmp_path)) as client:
        assert client.get("/api/settings").json()["MAX_ADDS"] == "321"


def test_settings_store_uses_data_dir_env(tmp_path, monkeypatch):
    # In Docker, SONGMIRROR_DATA_DIR points at the persistent /data volume — the store
    # must write there (not the container-relative ./data default) so wizard
    # config + secrets survive a rebuild.
    vol = tmp_path / "vol"
    monkeypatch.setenv("SONGMIRROR_DATA_DIR", str(vol))
    SettingsStore().save({"SPOTIFY_CLIENT_ID": "cid"})
    assert (vol / "settings.json").exists() and (vol / "app.env").exists()
    assert SettingsStore().get("SPOTIFY_CLIENT_ID") == "cid"  # a fresh store reads it back


def test_connector_token_paths_follow_env(tmp_path, monkeypatch):
    # In Docker these env vars point at the /data volume; the connectors must honor
    # them so tokens land on the persistent volume (and where the engine reads
    # them), not a relative ./data that's ephemeral inside the container.
    from songmirror.services.accounts.spotify import SpotifyConnector
    from songmirror.services.accounts.ytmusic import YTMusicConnector

    monkeypatch.setenv("SPOTIFY_TOKEN_CACHE", str(tmp_path / "sp_token"))
    monkeypatch.setenv("YTMUSIC_AUTH_FILE", str(tmp_path / "yt.json"))
    store = SettingsStore(dir=tmp_path)
    assert SpotifyConnector(store)._token_cache() == str(tmp_path / "sp_token")
    assert YTMusicConnector(store)._auth_file() == str(tmp_path / "yt.json")


def test_apple_ensure_storefront_backfills(monkeypatch, tmp_path):
    # A blank storefront is auto-detected from /v1/me/storefront; an explicit one
    # is left untouched.
    from songmirror.services.accounts.apple import AppleConnector

    store = SettingsStore(dir=tmp_path)
    store.save({"APPLE_BEARER_TOKEN": "b", "APPLE_USER_TOKEN": "u"})

    class FakeResp:
        ok = True

        @staticmethod
        def json():
            return {"data": [{"id": "bd", "type": "storefronts"}]}

    monkeypatch.setattr("songmirror.services.accounts.apple.requests.get", lambda *a, **k: FakeResp())
    AppleConnector(store)._ensure_storefront()
    assert store.get("APPLE_STOREFRONT") == "bd"

    store.save({"APPLE_STOREFRONT": "gb"})  # explicit value survives
    AppleConnector(store)._ensure_storefront()
    assert store.get("APPLE_STOREFRONT") == "gb"


def test_spotify_connect_accepts_web_session_without_oauth_redirect(tmp_path, monkeypatch):
    from songmirror.services.accounts.base import ConnStatus
    from songmirror.services.accounts.spotify import SpotifyConnector

    seen = {}

    def submit(_self, values):
        seen.update(values)
        return ConnStatus("connected", "signed-in web session · no developer API")

    monkeypatch.setattr(SpotifyConnector, "submit", submit)
    with TestClient(_app(tmp_path)) as client:
        result = client.post(
            "/api/accounts/spotify/connect", json={"SPOTIFY_SP_DC": "cookie-value"}
        ).json()

    assert seen == {"SPOTIFY_SP_DC": "cookie-value"}
    assert result == {
        "kind": "token_paste",
        "state": "connected",
        "detail": "signed-in web session · no developer API",
    }


def test_spotify_connect_without_cookie_is_a_friendly_error(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        result = client.post("/api/accounts/spotify/connect").json()

    assert result["kind"] == "token_paste"
    assert result["state"] == "error"
    assert "sp_dc" in result["detail"]


def test_oauth_callback_handles_provider_error(tmp_path):
    # Spotify (or the user denying) can bounce back with ?error=... instead of a
    # code — the callback must render a friendly page, not a 500 with a raw
    # "Internal Server Error".
    store = SettingsStore(dir=tmp_path)
    store.save({"SPOTIFY_CLIENT_ID": "cid", "SPOTIFY_CLIENT_SECRET": "sec"})
    with TestClient(create_app(settings=store)) as client:
        r = client.get("/oauth/spotify/callback?error=server_error")
        assert r.status_code == 200
        assert "server_error" in r.text and "Spotify" in r.text


def test_oauth_redirect_uses_configured_public_url(tmp_path, monkeypatch):
    """A remotely hosted Docker app must advertise its browser-reachable URL,
    not the container/request URL that happened to reach FastAPI."""
    from songmirror.services.accounts.spotify import SpotifyConnector

    seen = []
    monkeypatch.setenv("SPOTIFY_AUTH_MODE", "oauth")
    monkeypatch.setattr(
        SpotifyConnector,
        "begin_redirect",
        lambda _self, uri: (seen.append(uri), "https://accounts.spotify.test/authorize")[1],
    )
    monkeypatch.setenv("SONGMIRROR_PUBLIC_URL", "https://music.example.test/songmirror/")

    with TestClient(_app(tmp_path)) as client:
        result = client.post(
            "/api/accounts/spotify/connect",
            headers={"host": "127.0.0.1:8080"},
        ).json()

    expected = "https://music.example.test/songmirror/oauth/spotify/callback"
    assert seen == [expected]
    assert result["redirect_uri"] == expected


def test_spotify_oauth_mode_exposes_masked_env_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("SPOTIFY_AUTH_MODE", "oauth")
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "env-client")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "env-secret")

    with TestClient(_app(tmp_path)) as client:
        spotify = next(a for a in client.get("/api/accounts").json() if a["id"] == "spotify")

    assert spotify["auth_kind"] == "oauth_redirect"
    fields = {field["key"]: field for field in spotify["fields"]}
    assert fields["SPOTIFY_CLIENT_ID"]["value"] == "env-client"
    assert fields["SPOTIFY_CLIENT_ID"]["configured"] is True
    assert fields["SPOTIFY_CLIENT_SECRET"]["value"] == ""
    assert fields["SPOTIFY_CLIENT_SECRET"]["configured"] is True


def test_oauth_redirect_rejects_invalid_public_url(tmp_path, monkeypatch):
    from songmirror.services.accounts.spotify import SpotifyConnector

    monkeypatch.setenv("SPOTIFY_AUTH_MODE", "oauth")
    monkeypatch.setenv("SONGMIRROR_PUBLIC_URL", "music.example.test?from=compose")

    with TestClient(_app(tmp_path), raise_server_exceptions=False) as client:
        result = client.post("/api/accounts/spotify/connect")

    assert result.status_code == 500
    assert "SONGMIRROR_PUBLIC_URL" in result.json()["detail"]


def test_oauth_redirect_keeps_safe_loopback_fallback(tmp_path, monkeypatch):
    from songmirror.services.accounts.spotify import SpotifyConnector

    monkeypatch.delenv("SONGMIRROR_PUBLIC_URL", raising=False)
    monkeypatch.setenv("SPOTIFY_AUTH_MODE", "oauth")
    monkeypatch.setattr(SpotifyConnector, "begin_redirect", lambda _self, _uri: "https://example.test")

    with TestClient(_app(tmp_path)) as client:
        result = client.post(
            "/api/accounts/spotify/connect",
            headers={"host": "localhost:8888"},
        ).json()

    assert result["redirect_uri"] == "http://127.0.0.1:8888/oauth/spotify/callback"


def test_sync_run_queues(tmp_path, monkeypatch):
    import songmirror.services.sync_service as m

    async def fake(opts):
        return {"ok": True, "per_target": []}

    monkeypatch.setattr(m, "_run_pass_async", fake)
    with TestClient(_app(tmp_path)) as client:
        assert client.post("/api/sync/run?execute=0").status_code == 202


def test_auto_sync_pause_persists_across_restart(tmp_path):
    # Pausing auto-sync must survive a restart — the flag is persisted and the
    # scheduler reads it on boot, so it can't silently turn itself back on.
    store = SettingsStore(dir=tmp_path)
    with TestClient(create_app(settings=store)) as client:
        assert client.get("/api/sync/status").json()["master"] is True
        client.post("/api/sync/schedule", json={"action": "pause"})
        assert client.get("/api/sync/status").json()["master"] is False
    # A fresh app over the same persisted settings dir == a restart.
    with TestClient(create_app(settings=SettingsStore(dir=tmp_path))) as client:
        assert client.get("/api/sync/status").json()["master"] is False


def test_events_route_registered(tmp_path):
    # The live stream itself is verified in the browser E2E; TestClient can't
    # cleanly close an infinite SSE generator, so here we assert wiring + format.
    assert "/events" in _app(tmp_path).openapi()["paths"]


def test_links_crud(tmp_path):
    from songmirror.services.playlists import LinkStore

    app = create_app(settings=SettingsStore(dir=tmp_path), links=LinkStore(dir=tmp_path))
    with TestClient(app) as client:
        assert client.get("/api/links").json() == []
        lid = client.put("/api/links", json={"name": "Pair", "members": {"spotify": "s1"}}).json()["id"]
        assert lid
        assert len(client.get("/api/links").json()) == 1
        assert client.delete(f"/api/links/{lid}").json() == {"ok": True}
        assert client.get("/api/links").json() == []


def test_syncs_crud(tmp_path):
    # Fresh installs start with NO syncs (no auto-seeded "Default"); jobs are
    # created, merge-updated, and deleted via CRUD.
    store = SyncStore(dir=tmp_path)
    with TestClient(create_app(settings=SettingsStore(dir=tmp_path), syncs=store)) as client:
        assert client.get("/api/syncs").json() == []
        jid = client.post("/api/syncs", json={"name": "Workout", "mode": "oneway", "source": "apple"}).json()["id"]
        assert jid
        client.put(f"/api/syncs/{jid}", json={"enabled": False})
        got = next(j for j in client.get("/api/syncs").json() if j["id"] == jid)
        assert got["enabled"] is False and got["source"] == "apple"  # merge-update kept source
        client.delete(f"/api/syncs/{jid}")
        assert jid not in [j["id"] for j in client.get("/api/syncs").json()]


def test_download_dir_prefers_container_override(tmp_path, monkeypatch):
    # In Docker the download path is a container bind-mount (/music). An
    # SONGMIRROR_DOWNLOAD_DIR override must win over a UI-saved DOWNLOAD_DIR — inside
    # the Linux container that value can be a host path (a Windows F:\ path) that
    # spotDL would otherwise write to the ephemeral container filesystem, never
    # reaching the mounted volume. Non-Docker: unset, so the UI value is used.
    from songmirror.services.sync_service import SyncService
    from songmirror.services.syncs import SyncJob

    store = SettingsStore(dir=tmp_path)
    store.save({"DOWNLOAD_DIR": "F:\\Torrent\\Music"})
    svc = SyncService(store, None, syncs=SyncStore(dir=tmp_path))
    job = SyncJob(name="T", download=True)

    monkeypatch.setenv("SONGMIRROR_DOWNLOAD_DIR", "/music")
    assert svc._opts_for(job, execute=True).download_dir == "/music"
    monkeypatch.delenv("SONGMIRROR_DOWNLOAD_DIR")
    assert svc._opts_for(job, execute=True).download_dir == "F:\\Torrent\\Music"
    job.download = False  # opted out -> no download dir regardless of config
    assert svc._opts_for(job, execute=True).download_dir == ""


def test_spotify_client_raises_instead_of_prompting(monkeypatch):
    # A cached token whose scope doesn't cover the request (a read-only token vs
    # an N-way writable pass) must fail with a clear TargetAuthError — never
    # spotipy's interactive input(), which EOFErrors in a headless server.
    import pytest

    import songmirror.engine.spotify as sp
    from songmirror.engine.targets.base import TargetAuthError

    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "c")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "s")

    class FakeOAuth:
        def __init__(self, **k):
            pass

        def get_cached_token(self):
            return {"scope": "playlist-read-private"}

        def validate_token(self, t):
            return None  # scope mismatch -> spotipy would re-auth interactively

    monkeypatch.setattr(sp, "SpotifyOAuth", FakeOAuth)
    with pytest.raises(TargetAuthError):
        sp.client(writable=True)


def test_transfers_start_and_status(tmp_path, monkeypatch):
    from songmirror.services.transfers import TransferService

    # No providers -> the job errors fast (no network); exercises the REAL submit
    # path (asyncio.create_task) so the async-endpoint requirement can't regress.
    monkeypatch.setattr(TransferService, "_build", lambda self, pid, opts: None)
    with TestClient(_app(tmp_path)) as client:
        r = client.post("/api/transfers", json={"source_provider": "apple", "source_playlist_id": "p1",
                                                "dest_provider": "ytmusic", "dest_playlist_id": "p2"})
        assert r.status_code == 202
        jid = r.json()["job_id"]
        assert jid
        g = client.get(f"/api/transfers/{jid}").json()
        assert g["id"] == jid and "status" in g
        assert "_dest_cache_file" not in g  # internal field hidden from the API


def test_sse_payload_format():
    from songmirror.engine.logs import Event
    from songmirror.web.routers.events import _fmt

    line = _fmt(Event(1.0, "add", "apple", "Song - Artist"))
    assert line.startswith("data: ") and line.endswith("\n\n")
    import json
    payload = json.loads(line[len("data: "):].strip())
    assert payload["kind"] == "add" and payload["tag"] == "apple"
