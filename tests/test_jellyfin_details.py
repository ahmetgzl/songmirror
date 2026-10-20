"""Read-only Jellyfin inspection uses the same API-key scope as browsing."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests
from fastapi.testclient import TestClient

from songmirror.engine import jellyfin
from songmirror.services.playlists import PlaylistService
from songmirror.services.settings import SettingsStore
from songmirror.web import create_app


SERVER = "http://jellyfin.test/media"
KEY = "private-test-key"
PLAYLIST = {"Id": "mix", "Name": "Mix", "Overview": "<p>Evening &amp; morning</p>",
            "Type": "Playlist", "ImageTags": {"Primary": "cover"}}
TRACKS = [{"Id": str(index // 2), "Name": f"Track {index}", "Artists": ["One", "Two"],
           "Album": "Album", "AlbumId": "album", "AlbumPrimaryImageTag": "art",
           "RunTimeTicks": 123_456_789, "Type": "Audio"} for index in range(5)]


def response(url, body, status=200):
    result = requests.Response()
    result.url, result.status_code = url, status
    result._content = json.dumps(body).encode()
    return result


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    for key in ("JELLYFIN_URL", "JELLYFIN_API_KEY", "JELLYFIN_USER_ID", "SONGMIRROR_ENV_FILE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SONG_CACHE_FILE", str(tmp_path / "cache.db"))
    monkeypatch.setattr("songmirror.web.load_dotenv", lambda: False)
    for method in ("post", "patch", "delete"):
        monkeypatch.setattr(jellyfin.requests, method, lambda *a, **kw: pytest.fail("Inspection must never write to Jellyfin"))


@pytest.mark.parametrize("identity", ["jellyfin", "profile_default_jellyfin"])
@pytest.mark.parametrize("user", ["", "a" * 32])
def test_inspect_pages_full_read_cache_refresh_and_read_only(tmp_path, monkeypatch, identity, user):
    calls = []

    def get(url, headers=None, params=None, timeout=None):
        assert url == SERVER + "/Items"
        assert f'Token="{KEY}"' in headers["Authorization"]
        assert params.get("UserId", "") == user
        assert "SortBy" not in params  # Preserve the server's playlist ordering.
        calls.append(params)
        if "Ids" in params:
            assert params["Ids"] == "mix"
            return response(url, {"Items": [PLAYLIST], "TotalRecordCount": 1})
        assert params["ParentId"] == "mix"
        assert params["IncludeItemTypes"] == "Audio"
        start = params["StartIndex"]
        # A short server page must not hide the remaining tracks.
        return response(url, {"Items": TRACKS[start:start + 3], "TotalRecordCount": 5})

    monkeypatch.setattr(jellyfin.requests, "get", get)
    settings = SettingsStore(dir=tmp_path, project_env=False)
    settings.save({"JELLYFIN_URL": SERVER, "JELLYFIN_API_KEY": KEY, "JELLYFIN_USER_ID": user})
    with TestClient(create_app(settings=settings)) as client:
        url = f"/api/playlists/{identity}/mix"
        first = client.get(url, params={"page_size": 20, "expected_count": 5})
        assert first.status_code == 200
        page = first.json()
        assert page["editable"] is False
        assert page["description"] == "Evening & morning"
        assert page["count"] == 5
        assert page["next_cursor"] == "3"
        assert page["complete"] is False
        assert [track["id"] for track in page["tracks"]] == ["0", "0", "1"]
        assert [track["position"] for track in page["tracks"]] == [0, 1, 2]
        assert page["tracks"][0]["artist"] == "One, Two"
        assert page["tracks"][0]["duration_ms"] == 12_345
        assert page["tracks"][0]["image"] == SERVER + "/Items/album/Images/Primary"
        assert page["tracks"][0]["external_url"] == SERVER + "/web/#/details?id=0"
        assert KEY not in first.text
        second = client.get(url, params={"page_size": 20, "cursor": "3", "offset": 3})
        assert second.status_code == 200
        assert second.json()["complete"] is True
        assert [track["position"] for track in second.json()["tracks"]] == [3, 4]
        assert [call["Limit"] for call in calls if "ParentId" in call] == [20, 20]
        full = client.get(url)
        assert full.status_code == 200
        assert len(full.json()["tracks"]) == 5
        before = len(calls)
        assert len(client.get(url, params={"page_size": 20, "expected_count": 5}).json()["tracks"]) == 5
        assert len(calls) == before
        assert client.get(url, params={"page_size": 20, "refresh": True}).status_code == 200
        assert len(calls) > before
        before = len(calls)
        assert client.patch(url, json={"name": "New name"}).status_code == 403
        assert client.request("DELETE", url + "/tracks", json={"position": 0, "track_id": "0"}).status_code == 403
        assert client.get(url + "/export").status_code == 403
        assert len(calls) == before


@pytest.mark.parametrize("status,expected", [(401, "API key"), (403, "permissions"), (503, "HTTP 503")])
def test_detail_failure_is_actionable_and_never_leaks_the_key(tmp_path, monkeypatch, status, expected):
    settings = SettingsStore(dir=tmp_path, project_env=False)
    settings.save({"JELLYFIN_URL": SERVER, "JELLYFIN_API_KEY": KEY})
    monkeypatch.setattr(jellyfin.requests, "get", lambda url, **kw: response(url, {}, status))
    with TestClient(create_app(settings=settings)) as client:
        result = client.get("/api/playlists/jellyfin/mix", params={"page_size": 20})
    assert result.status_code == 502
    assert expected in result.json()["detail"]
    assert KEY not in result.text


@pytest.mark.parametrize("items,total", [(None, 5), (["invalid"], 5), ([{"Name": "No id"}], 5), ([], 5)])
def test_invalid_or_truncated_tracks_do_not_look_complete(tmp_path, monkeypatch, items, total):
    settings = SettingsStore(dir=tmp_path, project_env=False)
    settings.save({"JELLYFIN_URL": SERVER, "JELLYFIN_API_KEY": KEY})

    def get(url, params=None, **kw):
        return response(url, {"Items": [PLAYLIST]} if "Ids" in params else {"Items": items, "TotalRecordCount": total})

    monkeypatch.setattr(jellyfin.requests, "get", get)
    with TestClient(create_app(settings=settings)) as client:
        result = client.get("/api/playlists/jellyfin/mix", params={"page_size": 20})
    assert result.status_code == 502


def test_missing_empty_and_invalid_cursor_are_distinct(tmp_path, monkeypatch):
    settings = SettingsStore(dir=tmp_path, project_env=False)
    settings.save({"JELLYFIN_URL": SERVER, "JELLYFIN_API_KEY": KEY})

    def get(url, params=None, **kw):
        return response(url, {"Items": [PLAYLIST] if params.get("Ids") == "mix" else [], "TotalRecordCount": 0})

    monkeypatch.setattr(jellyfin.requests, "get", get)
    with TestClient(create_app(settings=settings)) as client:
        assert client.get("/api/playlists/jellyfin/missing", params={"page_size": 20}).status_code == 404
        empty = client.get("/api/playlists/jellyfin/mix", params={"page_size": 20}).json()
        assert empty["count"] == 0 and empty["tracks"] == [] and empty["complete"] is True
        for cursor in ("nope", "2"):
            assert client.get("/api/playlists/jellyfin/mix", params={"page_size": 20, "offset": 3, "cursor": cursor}).status_code == 409


def test_details_use_each_profiles_server_and_credentials(tmp_path, monkeypatch):
    settings = SettingsStore(dir=tmp_path, project_env=False)
    settings.save({"JELLYFIN_URL": SERVER, "JELLYFIN_API_KEY": KEY})
    app = create_app(settings=settings)
    other = app.state.account_profiles.create("jellyfin", "Other server")
    app.state.account_profiles.settings_for(other.id).save({
        "JELLYFIN_URL": "http://other.test", "JELLYFIN_API_KEY": "other-key", "JELLYFIN_USER_ID": "other-user",
    })

    def get(url, headers=None, params=None, **kw):
        second = url.startswith("http://other.test/")
        assert ('Token="other-key"' if second else f'Token="{KEY}"') in headers["Authorization"]
        assert params.get("UserId", "") == ("other-user" if second else "")
        return response(url, {"Items": [PLAYLIST]} if "Ids" in params else {"Items": TRACKS, "TotalRecordCount": 5})

    monkeypatch.setattr(jellyfin.requests, "get", get)
    service = PlaylistService(settings, app.state.account_profiles)
    assert service.detail_page("jellyfin", "mix")["external_url"].startswith(SERVER)
    assert service.detail_page(other.id, "mix")["external_url"].startswith("http://other.test/")
    assert service.detail_page("jellyfin", "mix")["external_url"].startswith(SERVER)


def test_jellyfin_browse_details_and_cache_do_not_wait_for_an_active_profile(tmp_path, monkeypatch):
    # Deployment-only credentials must remain available while a custom account
    # owns the environment lock and temporarily exposes different credentials.
    monkeypatch.setenv("JELLYFIN_URL", SERVER)
    monkeypatch.setenv("JELLYFIN_API_KEY", KEY)
    settings = SettingsStore(dir=tmp_path, project_env=False)
    app = create_app(settings=settings)
    profiles = app.state.account_profiles
    other = profiles.create("jellyfin", "Other server")
    profiles.settings_for(other.id).save({"JELLYFIN_URL": "http://other.test", "JELLYFIN_API_KEY": "other-key"})
    active, release = threading.Event(), threading.Event()

    def hold_profile():
        with profiles.activate(other.id):
            active.set()
            release.wait(timeout=10)

    def get(url, headers=None, params=None, **kw):
        assert url == SERVER + "/Items"
        assert f'Token="{KEY}"' in headers["Authorization"]
        if params.get("IncludeItemTypes") == "Playlist":
            return response(url, {"Items": [PLAYLIST], "TotalRecordCount": 1})
        return response(url, {"Items": [] if params["Limit"] == 0 else TRACKS, "TotalRecordCount": 5})

    monkeypatch.setattr(jellyfin.requests, "get", get)
    service = PlaylistService(settings, profiles)

    def inspect():
        assert service.browse("jellyfin")[0]["count"] == 5
        assert len(service.detail_page("jellyfin", "mix")["tracks"]) == 5
        assert len(service.detail("jellyfin", "mix")["tracks"]) == 5
        assert service.detail_page("jellyfin", "mix")["complete"] is True

    holder = threading.Thread(target=hold_profile)
    holder.start()
    try:
        assert active.wait(timeout=2)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(inspect)
            try:
                future.result(timeout=2)
            finally:
                release.set()
    finally:
        release.set()
        holder.join(timeout=2)
    assert not holder.is_alive()


def test_default_connection_includes_env_loaded_during_startup(tmp_path, monkeypatch):
    def deployment_env():
        monkeypatch.setenv("JELLYFIN_URL", SERVER)
        monkeypatch.setenv("JELLYFIN_API_KEY", KEY)

    monkeypatch.setattr("songmirror.web.load_dotenv", deployment_env)

    def get(url, headers=None, params=None, **kw):
        assert url == SERVER + "/Items"
        assert f'Token="{KEY}"' in headers["Authorization"]
        return response(url, {"Items": [PLAYLIST]} if "Ids" in params else {"Items": TRACKS, "TotalRecordCount": 5})

    monkeypatch.setattr(jellyfin.requests, "get", get)
    with TestClient(create_app(settings=SettingsStore(dir=tmp_path, project_env=False))) as client:
        result = client.get("/api/playlists/jellyfin/mix", params={"page_size": 20})
    assert result.status_code == 200
    assert len(result.json()["tracks"]) == 5
