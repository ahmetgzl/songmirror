"""Resolve-cache provenance, the editable store, and its HTTP surface."""

import json

import pytest
from fastapi.testclient import TestClient

from songmirror.engine.runner import MATCHING_CACHE_VERSION, load_cache, save_cache
from songmirror.services.resolve_cache import (
    ResolveCacheBusy, ResolveCacheError, ResolveCacheStore,
)
from songmirror.services.settings import SettingsStore
from songmirror.web import create_app


# -- provenance round-trip ---------------------------------------------------
def test_legacy_automatic_matches_are_invalidated(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text(json.dumps({"isrc": {}, "search": {"a|b": "1"}}), encoding="utf-8")
    cache = load_cache(str(path))
    assert cache["manual"] == set()
    assert cache["search"] == {}
    assert cache["dirty"] is True


def test_recording_match_upgrade_invalidates_previous_automatic_ids(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text(json.dumps({
        "matching_version": 1,
        "isrc": {"ISRC": [{"id": "wrong-performer"}]},
        "search": {"song|artist": "old-automatic", "chosen|artist": "user-choice"},
        "manual": ["chosen|artist"],
    }), encoding="utf-8")
    cache = load_cache(path)
    assert cache["isrc"] == {}
    assert cache["search"] == {"chosen|artist": "user-choice"}
    assert cache["manual"] == {"chosen|artist"}
    save_cache(path, cache)
    assert json.loads(path.read_text("utf-8"))["matching_version"] > 1


def test_matching_cache_version_bump_discards_automatic_mappings(tmp_path):
    """Bumping MATCHING_CACHE_VERSION drops stale automatic search ids, keeps manuals."""
    path = tmp_path / "cache.json"
    previous = MATCHING_CACHE_VERSION - 1
    path.write_text(json.dumps({
        "matching_version": previous,
        "isrc": {"ISRC": [{"id": "stale-isrc"}]},
        "search": {
            "auto|artist": "old-automatic",
            "chosen|artist": "user-choice",
        },
        "manual": ["chosen|artist"],
    }), encoding="utf-8")
    cache = load_cache(path)
    assert cache["isrc"] == {}
    assert cache["search"] == {"chosen|artist": "user-choice"}
    assert cache["manual"] == {"chosen|artist"}
    assert cache["dirty"] is True
    save_cache(path, cache)
    written = json.loads(path.read_text("utf-8"))
    assert written["matching_version"] == MATCHING_CACHE_VERSION
    assert written["search"] == {"chosen|artist": "user-choice"}


def test_legacy_migration_preserves_manual_choices_and_refreshes_isrc_metadata(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text(json.dumps({
        "isrc": {"ISRC": [{"id": "version-was-omitted"}]},
        "search": {"a|b": "automatic", "c|d": "chosen", "e|f": None},
        "manual": ["c|d"],
    }), encoding="utf-8")
    cache = load_cache(path)
    assert cache["isrc"] == {}
    assert cache["search"] == {"c|d": "chosen"}
    assert cache["manual"] == {"c|d"}
    save_cache(path, cache)
    reloaded = load_cache(path)
    assert reloaded["search"] == {"c|d": "chosen"}
    assert reloaded["dirty"] is False


def test_manual_keys_round_trip_through_save_and_load(tmp_path):
    path = str(tmp_path / "cache.json")
    cache = load_cache(path)
    cache["search"]["song|artist"] = "trk1"
    cache["manual"].add("song|artist")
    cache["dirty"] = True
    save_cache(path, cache)

    # Serialized as a sorted list so the file stays diffable, and still carries
    # the two keys every existing reader expects.
    written = json.loads(open(path, encoding="utf-8").read())
    assert written == {"matching_version": MATCHING_CACHE_VERSION, "isrc": {}, "search": {"song|artist": "trk1"}, "manual": ["song|artist"]}
    assert load_cache(path)["manual"] == {"song|artist"}


def test_a_clean_cache_is_not_rewritten(tmp_path):
    path = str(tmp_path / "cache.json")
    save_cache(path, load_cache(path))
    with pytest.raises(FileNotFoundError):
        open(path, encoding="utf-8")


def test_stale_worker_preserves_manual_resolution_and_unrelated_entries(tmp_path, monkeypatch):
    store, path = _store(tmp_path, monkeypatch, {"lost|artist": None, "history|artist": "11"})
    worker = load_cache(path)
    store.set("deezer", "lost|artist", "123")
    other = load_cache(path)
    other["search"]["another|artist"] = "22"
    other["dirty"] = True
    save_cache(path, other)

    worker["search"]["new|artist"] = "33"
    worker["dirty"] = True
    save_cache(path, worker)

    written = load_cache(path)
    assert written["search"] == {
        "lost|artist": "123", "history|artist": "11",
        "another|artist": "22", "new|artist": "33",
    }
    assert written["manual"] == {"lost|artist"}


def test_stale_worker_does_not_resurrect_deleted_manual_mapping(tmp_path, monkeypatch):
    store, path = _store(tmp_path, monkeypatch, {"chosen|artist": "123"}, manual=["chosen|artist"])
    worker = load_cache(path)
    store.delete("deezer", "chosen|artist")
    worker["search"]["new|artist"] = "456"
    worker["dirty"] = True
    save_cache(path, worker)
    written = load_cache(path)
    assert written["search"] == {"new|artist": "456"}
    assert written["manual"] == set()


def test_saving_a_partial_cache_preserves_existing_history(tmp_path, monkeypatch):
    _store_obj, path = _store(tmp_path, monkeypatch, {"chosen|artist": "123", "old|artist": "456"}, manual=["chosen|artist"])
    save_cache(path, {"search": {"new|artist": "789"}, "isrc": {}, "dirty": True})
    written = load_cache(path)
    assert written["search"] == {"chosen|artist": "123", "old|artist": "456", "new|artist": "789"}
    assert written["manual"] == {"chosen|artist"}


def test_stale_manual_value_does_not_overwrite_a_newer_choice(tmp_path, monkeypatch):
    store, path = _store(tmp_path, monkeypatch, {"chosen|artist": "123"}, manual=["chosen|artist"])
    worker = load_cache(path)
    store.set("deezer", "chosen|artist", "456")
    worker["search"]["new|artist"] = "789"
    worker["dirty"] = True
    save_cache(path, worker)
    assert load_cache(path)["search"]["chosen|artist"] == "456"


@pytest.mark.parametrize("delete", [False, True])
def test_stale_manual_url_normalization_preserves_newer_edit(tmp_path, monkeypatch, delete):
    from songmirror.engine.targets.deezer import DeezerTarget

    key = "chosen|artist"
    store, path = _store(tmp_path, monkeypatch, {key: "https://www.deezer.com/track/123"}, manual=[key])
    worker = load_cache(path)
    worker["search"][key] = DeezerTarget.normalize_manual_track_id(worker["search"][key])
    worker["dirty"] = True
    if delete:
        store.delete("deezer", key)
    else:
        store.set("deezer", key, "456")
    save_cache(path, worker)
    assert load_cache(path)["search"] == ({} if delete else {key: "456"})


@pytest.mark.parametrize("chosen", ["123", "789"])
def test_explicit_manual_edit_wins_over_concurrent_match(tmp_path, monkeypatch, chosen):
    store, path = _store(tmp_path, monkeypatch, {"chosen|artist": "123"}, manual=["chosen|artist"])
    editing = load_cache(path)
    store.set("deezer", "chosen|artist", "456")
    # The user can also explicitly reaffirm the original choice.
    editing["search"]["chosen|artist"] = chosen
    editing["dirty"] = True
    save_cache(path, editing, manual_keys={"chosen|artist"})
    assert load_cache(path)["search"]["chosen|artist"] == chosen


def test_failed_atomic_replace_preserves_cache_and_pending_changes(tmp_path, monkeypatch):
    from songmirror.engine import resolve_cache

    _store_obj, path = _store(tmp_path, monkeypatch, {"chosen|artist": "123"}, manual=["chosen|artist"])
    original = open(path, "rb").read()
    worker = load_cache(path)
    worker["search"]["new|artist"] = "456"
    worker["dirty"] = True

    def fail_replace(*_args):
        raise OSError("disk unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(resolve_cache.os, "replace", fail_replace)
        with pytest.raises(OSError, match="disk unavailable"):
            save_cache(path, worker)
    assert open(path, "rb").read() == original
    assert worker["dirty"] is True
    assert not list(tmp_path.glob("*.tmp"))
    save_cache(path, worker)
    assert load_cache(path)["search"]["new|artist"] == "456"


def test_corrupt_cache_is_not_replaced_with_a_partial_snapshot(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text('{"search": {"chosen|artist":', encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(json.JSONDecodeError):
        save_cache(path, {"search": {"new|artist": "123"}, "isrc": {}, "dirty": True})
    assert path.read_bytes() == before


def test_concurrent_cache_writers_keep_every_update(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    path = tmp_path / "cache.json"
    ready = Barrier(8)

    def write(index):
        worker = load_cache(path)
        ready.wait(timeout=5)
        key = f"song{index}|artist"
        worker["search"][key] = str(index)
        worker["manual"].add(key)
        worker["dirty"] = True
        save_cache(path, worker)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(8)))
    written = load_cache(path)
    assert written["search"] == {f"song{index}|artist": str(index) for index in range(8)}
    assert written["manual"] == set(written["search"])


# -- the store ---------------------------------------------------------------
class _Sync:
    def __init__(self, running=False):
        self._running = running

    def status(self):
        return {"running": self._running}


# Every provider's cache path, so a test sees only the files it wrote and never
# the repository's own caches through a relative default.
_CACHE_ENV = {
    "spotify": "SPOTIFY_CACHE_FILE",
    "tidal": "TIDAL_CACHE_FILE",
    "qobuz": "QOBUZ_CACHE_FILE",
    "deezer": "DEEZER_CACHE_FILE",
    "amazon": "AMAZON_MUSIC_CACHE_FILE",
    "apple": "APPLE_CACHE_FILE",
    "ytmusic": "YTMUSIC_CACHE_FILE",
    "lastfm": "LASTFM_CACHE_FILE",
}


def _store(tmp_path, monkeypatch, rows, *, manual=(), provider="deezer", sync=None):
    for name, env_key in _CACHE_ENV.items():
        monkeypatch.setenv(env_key, str(tmp_path / f"{name}_cache.json"))
    path = str(tmp_path / f"{provider}_cache.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"matching_version": MATCHING_CACHE_VERSION, "isrc": {}, "search": rows, "manual": list(manual)}, f)
    return ResolveCacheStore(SettingsStore(dir=tmp_path), sync), path


_ROWS = {
    "midnight|aurora": "111",
    "sunrise|beacon": "222",
    "lost track|cinder": None,     # searched, found nothing
    "another lost|delta": None,
}


def test_providers_lists_only_services_with_a_cache_on_disk(tmp_path, monkeypatch):
    store, _path = _store(tmp_path, monkeypatch, _ROWS, manual=["sunrise|beacon"])
    rows = {row["id"]: row for row in store.providers()}
    assert set(rows) == {"deezer"}    # the other seven have no file
    assert rows["deezer"] == {
        "id": "deezer", "name": "Deezer", "total": 4, "manual": 1, "unmatched": 2,
    }


def test_entries_splits_the_key_and_links_the_resolved_id(tmp_path, monkeypatch):
    store, _path = _store(tmp_path, monkeypatch, {"midnight|aurora": "111"})
    row = store.entries("deezer")["entries"][0]
    assert row == {
        "key": "midnight|aurora", "name": "midnight", "artist": "aurora",
        "target_id": "111", "manual": False,
        "url": "https://www.deezer.com/track/111",
    }


def test_an_unmatched_entry_has_no_link(tmp_path, monkeypatch):
    store, _path = _store(tmp_path, monkeypatch, {"lost track|cinder": None})
    assert store.entries("deezer")["entries"][0]["url"] == ""


@pytest.mark.parametrize("kind, expected", [
    ("all", {"another lost", "lost track", "midnight", "sunrise"}),
    ("manual", {"sunrise"}),
    ("unmatched", {"another lost", "lost track"}),
])
def test_entries_filters_by_kind(tmp_path, monkeypatch, kind, expected):
    store, _path = _store(tmp_path, monkeypatch, _ROWS, manual=["sunrise|beacon"])
    page = store.entries("deezer", kind=kind)
    assert {row["name"] for row in page["entries"]} == expected
    assert page["total"] == len(expected)


def test_entries_searches_both_the_key_and_the_resolved_id(tmp_path, monkeypatch):
    store, _path = _store(tmp_path, monkeypatch, _ROWS)
    assert {r["name"] for r in store.entries("deezer", query="aurora")["entries"]} == {"midnight"}
    assert {r["name"] for r in store.entries("deezer", query="222")["entries"]} == {"sunrise"}


def test_entries_pages_and_reports_the_unpaged_total(tmp_path, monkeypatch):
    store, _path = _store(tmp_path, monkeypatch, _ROWS)
    page = store.entries("deezer", offset=1, limit=2)
    assert page["total"] == 4
    # Sorted by name, so offset 1 skips "another lost".
    assert [row["name"] for row in page["entries"]] == ["lost track", "midnight"]


def test_an_unknown_filter_is_rejected(tmp_path, monkeypatch):
    store, _path = _store(tmp_path, monkeypatch, _ROWS)
    with pytest.raises(ResolveCacheError):
        store.entries("deezer", kind="everything")


def test_set_normalizes_a_pasted_url_and_marks_the_entry_manual(tmp_path, monkeypatch):
    store, path = _store(tmp_path, monkeypatch, {"lost track|cinder": None})
    row = store.set("deezer", "lost track|cinder", "https://www.deezer.com/en/track/98765")
    assert row["target_id"] == "98765"    # the numeric id, not the pasted URL
    assert row["manual"] is True
    cache = load_cache(path)
    assert cache["search"]["lost track|cinder"] == "98765"
    assert cache["manual"] == {"lost track|cinder"}


def test_set_rejects_an_id_the_provider_cannot_parse(tmp_path, monkeypatch):
    store, _path = _store(tmp_path, monkeypatch, {"lost track|cinder": None})
    with pytest.raises(ResolveCacheError):
        store.set("deezer", "lost track|cinder", "definitely not a deezer track")


def test_set_refuses_a_key_that_is_no_longer_cached(tmp_path, monkeypatch):
    store, _path = _store(tmp_path, monkeypatch, {"midnight|aurora": "111"})
    with pytest.raises(ResolveCacheError):
        store.set("deezer", "gone|missing", "98765")


def test_delete_forgets_the_mapping_and_its_provenance(tmp_path, monkeypatch):
    store, path = _store(tmp_path, monkeypatch, _ROWS, manual=["sunrise|beacon"])
    assert store.delete("deezer", "sunrise|beacon") == {"ok": True}
    cache = load_cache(path)
    assert "sunrise|beacon" not in cache["search"]
    assert cache["manual"] == set()


def test_deleting_a_missing_key_is_not_an_error(tmp_path, monkeypatch):
    store, _path = _store(tmp_path, monkeypatch, _ROWS)
    assert store.delete("deezer", "never|existed") == {"ok": False}


def test_clear_unmatched_drops_only_the_negative_entries(tmp_path, monkeypatch):
    store, path = _store(tmp_path, monkeypatch, _ROWS, manual=["sunrise|beacon"])
    assert store.clear_unmatched("deezer") == {"removed": 2, "removed_isrc": 0}
    cache = load_cache(path)
    assert set(cache["search"]) == {"midnight|aurora", "sunrise|beacon"}
    assert cache["manual"] == {"sunrise|beacon"}


def test_clear_unmatched_also_drops_empty_isrc_lookups(tmp_path, monkeypatch):
    # An ISRC that resolved to no candidates is just as sticky as a search miss,
    # and it blocks the more accurate ISRC path from ever being retried.
    store, path = _store(tmp_path, monkeypatch, {"midnight|aurora": "111"})
    cache = load_cache(path)
    cache["isrc"] = {"GBAAA0000001": [], "GBAAA0000002": [{"id": "222"}]}
    cache["dirty"] = True
    save_cache(path, cache)

    assert store.clear_unmatched("deezer") == {"removed": 0, "removed_isrc": 1}
    after = load_cache(path)
    assert set(after["isrc"]) == {"GBAAA0000002"}    # the productive lookup stays
    assert set(after["search"]) == {"midnight|aurora"}


@pytest.mark.parametrize("call", [
    lambda store: store.set("deezer", "midnight|aurora", "98765"),
    lambda store: store.delete("deezer", "midnight|aurora"),
    lambda store: store.clear_unmatched("deezer"),
])
def test_writes_are_refused_while_a_sync_owns_the_caches(tmp_path, monkeypatch, call):
    # A pass holds the cache in memory for its whole duration and writes it back
    # at the end, so an edit made in between would be silently overwritten.
    store, _path = _store(tmp_path, monkeypatch, _ROWS, sync=_Sync(running=True))
    with pytest.raises(ResolveCacheBusy):
        call(store)


def test_reads_still_work_while_a_sync_is_running(tmp_path, monkeypatch):
    store, _path = _store(tmp_path, monkeypatch, _ROWS, sync=_Sync(running=True))
    assert store.entries("deezer")["total"] == 4


# -- HTTP surface ------------------------------------------------------------
def _client(tmp_path, monkeypatch, rows, *, manual=(), sync=None):
    store, path = _store(tmp_path, monkeypatch, rows, manual=manual, sync=sync)
    app = create_app(settings=SettingsStore(dir=tmp_path), resolve_cache=store)
    return TestClient(app), path


def test_api_lists_providers_and_pages_entries(tmp_path, monkeypatch):
    client, _path = _client(tmp_path, monkeypatch, _ROWS, manual=["sunrise|beacon"])
    with client:
        assert [row["id"] for row in client.get("/api/resolve-cache").json()] == ["deezer"]
        page = client.get("/api/resolve-cache/deezer", params={"kind": "unmatched"}).json()
    assert page["total"] == 2


def test_api_sets_and_deletes_an_entry_by_key_in_the_body(tmp_path, monkeypatch):
    # The key is "<name>|<artist>" and routinely contains slashes, so it cannot
    # ride in the path.
    client, path = _client(tmp_path, monkeypatch, {"lost track|cinder": None})
    with client:
        set_response = client.put(
            "/api/resolve-cache/deezer",
            json={"key": "lost track|cinder", "target_id": "98765"},
        )
        assert set_response.status_code == 200
        assert set_response.json()["target_id"] == "98765"

        delete_response = client.request(
            "DELETE", "/api/resolve-cache/deezer", json={"key": "lost track|cinder"})
    assert delete_response.json() == {"ok": True}
    assert load_cache(path)["search"] == {}


def test_api_clear_unmatched_reports_how_many_it_removed(tmp_path, monkeypatch):
    client, _path = _client(tmp_path, monkeypatch, _ROWS)
    with client:
        response = client.post("/api/resolve-cache/deezer/clear-unmatched")
    assert response.json() == {"removed": 2, "removed_isrc": 0}


def test_api_returns_409_while_a_sync_is_running(tmp_path, monkeypatch):
    client, _path = _client(tmp_path, monkeypatch, _ROWS, sync=_Sync(running=True))
    with client:
        response = client.put(
            "/api/resolve-cache/deezer", json={"key": "midnight|aurora", "target_id": "1"})
    assert response.status_code == 409
    assert "sync is running" in response.json()["detail"]


def test_api_rejects_a_bad_id_with_the_providers_own_message(tmp_path, monkeypatch):
    client, _path = _client(tmp_path, monkeypatch, {"lost track|cinder": None})
    with client:
        response = client.put(
            "/api/resolve-cache/deezer",
            json={"key": "lost track|cinder", "target_id": "nonsense"},
        )
    assert response.status_code == 422
    assert "Deezer" in response.json()["detail"]
