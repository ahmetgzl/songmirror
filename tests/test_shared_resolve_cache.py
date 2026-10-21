"""Same-provider catalog reuse without copying mappings or account credentials."""

import json
from pathlib import Path

import pytest

from songmirror.engine import archive, runner, targets
from songmirror.engine.config import parse_args
from songmirror.engine.resolve_cache import MATCHING_CACHE_VERSION, load_cache, load_target_cache, save_cache
from songmirror.engine.targets.base import MirrorTarget, _normalize
from songmirror.engine.targets.deezer import DeezerTarget
from songmirror.services.account_profiles import AccountProfileStore
from songmirror.services.import_matching import ImportMatcher
from songmirror.services.resolve_cache import ResolveCacheStore
from songmirror.services.settings import SettingsStore
from songmirror.services.transfers import transfer

KEY = "bohemian rhapsody|queen"
TRACK = {"id": "source", "name": "Bohemian Rhapsody", "artist": "Queen", "artists": ["Queen"],
         "duration_ms": 354000, "isrc": None}


def write_cache(path, search=None, *, manual=(), isrc=None, version=MATCHING_CACHE_VERSION):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"matching_version": version, "search": search or {},
                               "manual": list(manual), "isrc": isrc or {}}), encoding="utf-8")
    return path


@pytest.fixture
def accounts(tmp_path, monkeypatch):
    for key in ("DEEZER_CACHE_FILE", "TIDAL_CACHE_FILE", "SUBSONIC_CACHE_FILE", "APPLE_CACHE_FILE"):
        monkeypatch.setenv(key, "")
    settings = SettingsStore(dir=tmp_path)
    settings.save({"DEEZER_CACHE_FILE": str(tmp_path / "legacy-deezer.json")})
    profiles = AccountProfileStore(settings)
    first = profiles.create("deezer", "First")
    second = profiles.create("deezer", "Second")
    return settings, profiles, first, second


@pytest.mark.parametrize("local", [{}, {KEY: None}])
@pytest.mark.parametrize("manual", [False, True])
def test_positive_sibling_matches_fill_missing_and_negative_local_results(tmp_path, local, manual):
    primary = write_cache(tmp_path / "primary.json", local)
    sibling = write_cache(tmp_path / "sibling.json", {KEY: "https://www.deezer.com/track/123"},
                          manual=[KEY] if manual else [])
    original = sibling.read_bytes()
    cache = load_cache(primary, provider="deezer", fallbacks=[sibling])
    assert cache["search"][KEY] == "123"
    assert (KEY in cache["manual"]) is manual
    assert not cache["dirty"]
    cache["search"]["other|artist"] = "456"
    cache["dirty"] = True
    save_cache(primary, cache)
    # Inherited choices remain live in memory and are never copied into the
    # account's persisted cache. Donor updates/deletions therefore propagate.
    assert cache["search"][KEY] == "123"
    assert json.loads(primary.read_text())["search"].get(KEY) is None
    assert json.loads(primary.read_text())["manual"] == []
    assert sibling.read_bytes() == original


def test_donor_corrections_and_deletions_are_seen_without_stale_copies(tmp_path):
    primary, sibling = tmp_path / "new-account.json", tmp_path / "donor.json"
    write_cache(sibling, {KEY: "123"}, manual=[KEY])
    cache = load_cache(primary, provider="deezer", fallbacks=[sibling])
    cache["search"]["other|artist"], cache["dirty"] = "456", True
    save_cache(primary, cache)
    write_cache(sibling, {KEY: "789"}, manual=[KEY])
    assert load_cache(primary, provider="deezer", fallbacks=[sibling])["search"][KEY] == "789"
    write_cache(sibling)
    assert KEY not in load_cache(primary, provider="deezer", fallbacks=[sibling])["search"]


@pytest.mark.parametrize("manual", [False, True])
def test_local_positive_choices_always_win(tmp_path, manual):
    primary = write_cache(tmp_path / "local.json", {KEY: "111"}, manual=[KEY] if manual else [])
    sibling = write_cache(tmp_path / "sibling.json", {KEY: "222"}, manual=[KEY])
    cache = load_cache(primary, provider="deezer", fallbacks=[sibling])
    assert cache["search"][KEY] == "111"
    assert (KEY in cache["manual"]) is manual


@pytest.mark.parametrize("manuals,expected", [
    ([], None), ([0], "123"), ([0, 1], None),
])
def test_conflicting_sibling_choices_are_not_resolved_by_profile_order(tmp_path, manuals, expected):
    siblings = [write_cache(tmp_path / f"sibling-{index}.json", {KEY: str(123 + index)},
                            manual=[KEY] if index in manuals else []) for index in range(2)]
    for paths in (siblings, list(reversed(siblings))):
        cache = load_cache(tmp_path / "primary.json", provider="deezer", fallbacks=paths)
        assert cache["search"].get(KEY) == expected


def test_negative_invalid_and_outdated_automatic_matches_are_not_inherited(tmp_path):
    siblings = [write_cache(tmp_path / "negative.json", {KEY: None}),
                write_cache(tmp_path / "bad.json", {KEY: "https://wrong.example/track/123"}, manual=[KEY]),
                write_cache(tmp_path / "old.json", {KEY: "123"}, version=MATCHING_CACHE_VERSION - 1)]
    assert load_cache(tmp_path / "local.json", provider="deezer", fallbacks=siblings)["search"] == {}
    write_cache(siblings[-1], {KEY: "123"}, manual=[KEY], version=MATCHING_CACHE_VERSION - 1)
    assert load_cache(tmp_path / "local.json", provider="deezer", fallbacks=siblings)["search"][KEY] == "123"


def test_corrupt_donors_are_ignored_and_a_corrupt_primary_is_never_overwritten(tmp_path):
    sibling = tmp_path / "broken.json"
    sibling.write_text("{broken")
    primary = tmp_path / "local.json"
    cache = load_cache(primary, provider="deezer", fallbacks=[sibling])
    assert cache["search"] == {}
    cache["search"][KEY], cache["dirty"] = "123", True
    save_cache(primary, cache)
    assert sibling.read_text() == "{broken"
    with pytest.raises(ValueError):
        load_cache(sibling, provider="deezer", fallbacks=[primary])


def test_isrc_candidates_are_reused_and_merged_but_account_library_ids_are_excluded(tmp_path):
    siblings = [write_cache(tmp_path / f"sibling-{index}.json", isrc={"TEST": [
        {"id": str(123 + index), "name": TRACK["name"], "artist": "Queen", "duration_ms": 354000},
        {"id": "i.library-song", "name": "Private library song", "artist": "Artist"},
    ], "MISS": []}) for index in range(2)]
    primary = write_cache(tmp_path / "primary.json", isrc={"TEST": []})
    cache = load_cache(primary, provider="apple", fallbacks=siblings)
    assert {row["id"] for row in cache["isrc"]["TEST"]} == {"123", "124"}
    assert "MISS" not in cache["isrc"]
    cache["search"]["other|artist"], cache["dirty"] = "789", True
    save_cache(primary, cache)
    assert json.loads(primary.read_text())["isrc"] == {"TEST": []}


@pytest.mark.parametrize("malformed", [42, "not a candidate list", {"id": "999"}])
def test_malformed_sibling_isrc_lists_do_not_break_other_catalog_evidence(tmp_path, malformed):
    sibling = write_cache(tmp_path / "sibling.json", {KEY: "123"}, isrc={"TEST": malformed})
    cache = load_cache(tmp_path / "local.json", provider="deezer", fallbacks=[sibling])
    assert cache["search"][KEY] == "123"
    assert cache["isrc"] == {}


def test_malformed_sibling_isrc_metadata_is_not_used_by_recording_guards(tmp_path):
    valid = {"id": "123", "name": TRACK["name"], "artist": "Queen", "duration_ms": 354000}
    malformed = [None, "bad candidate", {**valid, "name": 42}, {**valid, "artist": []},
                 {**valid, "artists": 42}, {**valid, "artists": [42]},
                 {**valid, "name": "  "}, *({**valid, "duration_ms": duration}
                     for duration in ("354000", True, -1, float("inf"), float("nan"), 10 ** 400))]
    sibling = write_cache(tmp_path / "sibling.json", isrc={"TEST": [*malformed, valid]})
    original = sibling.read_bytes()
    cache = load_cache(tmp_path / "local.json", provider="deezer", fallbacks=[sibling])
    assert cache["isrc"] == {"TEST": [valid]}
    assert sibling.read_bytes() == original


def test_profile_paths_scopes_and_existing_files_are_preserved(accounts):
    settings, profiles, first, second = accounts
    first_path, second_path = profiles.resolve_cache_path(first.id), profiles.resolve_cache_path(second.id)
    assert Path(first_path).parent == profiles.profile_dir(first.id)
    assert Path(second_path).parent == profiles.profile_dir(second.id)
    write_cache(first_path, {KEY: "123"}, manual=[KEY])
    profiles.settings_for(first.id).save({"DEEZER_REFRESH_TOKEN": "first-secret"})
    profiles.settings_for(second.id).save({"DEEZER_REFRESH_TOKEN": "second-secret"})
    other = profiles.create("tidal", "Other provider")
    other_path = write_cache(profiles.resolve_cache_path(other.id), {KEY: "999"}, manual=[KEY])
    fallback_paths = profiles.cache_fallback_files(second.id, second_path)
    assert first_path in fallback_paths and str(other_path) not in fallback_paths
    store = ResolveCacheStore(settings, profiles=profiles)
    row = store.entries(second.id)["entries"][0]
    assert row["target_id"] == "123" and row["manual"]
    assert not Path(second_path).exists()
    assert {row["id"] for row in store.providers()} >= {first.id, second.id}
    with profiles.activate(second.id):
        import os
        assert os.environ["DEEZER_REFRESH_TOKEN"] == "second-secret"
    assert "first-secret" not in Path(first_path).read_text()
    assert "second-secret" not in Path(first_path).read_text()


@pytest.mark.parametrize("provider,identifier", [
    ("spotify", "4uLU6hMCjMI75M1A2tKUQC"), ("apple", "123"), ("tidal", "123"),
    ("qobuz", "123"), ("deezer", "123"), ("amazon", "B012345678"),
    ("ytmusic", "dQw4w9WgXcQ"), ("lastfm", "Queen\u241fBohemian Rhapsody"),
    ("listenbrainz", "mbid:12345678-1234-1234-1234-123456789abc"),
])
def test_each_global_catalog_reuses_its_own_sibling_profile_matches(accounts, provider, identifier):
    settings, profiles, _, _ = accounts
    first = profiles.create(provider, "Catalog donor")
    second = profiles.create(provider, "Catalog borrower")
    write_cache(profiles.resolve_cache_path(first.id), {KEY: identifier}, manual=[KEY])
    raw = MirrorTarget()
    raw.source, raw.cache_file = provider, profiles.resolve_cache_path(second.id)
    cache = load_target_cache(targets.AccountBoundTarget(raw, second, profiles))
    assert cache["search"][KEY] == identifier and KEY in cache["manual"]
    assert ResolveCacheStore(settings, profiles=profiles).entries(second.id)["entries"][0]["target_id"] == identifier
    assert not Path(raw.cache_file).exists()


def test_forgetting_an_inherited_match_is_local_and_does_not_reappear(accounts):
    settings, profiles, first, second = accounts
    donor = write_cache(profiles.resolve_cache_path(first.id), {KEY: "123"}, manual=[KEY])
    original = donor.read_bytes()
    store = ResolveCacheStore(settings, profiles=profiles)
    worker = store._load(second.id)[1]
    assert store.delete(second.id, KEY) == {"ok": True}
    assert store.entries(second.id)["total"] == 0
    # An old worker must not put the deleted inherited choice back.
    worker["search"]["new|artist"], worker["dirty"] = "456", True
    save_cache(profiles.resolve_cache_path(second.id), worker)
    assert all(row["key"] != KEY for row in store.entries(second.id)["entries"])
    assert donor.read_bytes() == original
    assert store.entries(first.id)["entries"][0]["target_id"] == "123"


def test_a_local_manual_override_does_not_change_the_donor(accounts):
    settings, profiles, first, second = accounts
    donor = write_cache(profiles.resolve_cache_path(first.id), {KEY: "123"}, manual=[KEY])
    store = ResolveCacheStore(settings, profiles=profiles)
    assert store.set(second.id, KEY, "https://www.deezer.com/track/456")["target_id"] == "456"
    assert store.entries(first.id)["entries"][0]["target_id"] == "123"
    assert store.entries(second.id)["entries"][0]["target_id"] == "456"


def test_stale_inherited_worker_preserves_a_new_local_manual_override(accounts):
    settings, profiles, first, second = accounts
    write_cache(profiles.resolve_cache_path(first.id), {KEY: "123"}, manual=[KEY])
    store = ResolveCacheStore(settings, profiles=profiles)
    worker = store._load(second.id)[1]
    store.set(second.id, KEY, "456")
    worker["search"]["other|artist"], worker["dirty"] = "789", True
    save_cache(profiles.resolve_cache_path(second.id), worker)
    assert worker["search"][KEY] == "456"
    assert store.entries(second.id, query=KEY)["entries"][0]["target_id"] == "456"


def test_legacy_default_and_custom_cache_overrides_are_both_available(accounts, tmp_path):
    settings, profiles, first, second = accounts
    legacy = write_cache(settings.get("DEEZER_CACHE_FILE"), {KEY: "123"}, manual=[KEY])
    custom = tmp_path / "custom-location" / "chosen.json"
    profiles.settings_for(first.id).save({"DEEZER_CACHE_FILE": str(custom)})
    write_cache(custom, {"other|artist": "456"}, manual=["other|artist"])
    store = ResolveCacheStore(settings, profiles=profiles)
    rows = store.entries(second.id)["entries"]
    assert {row["target_id"] for row in rows} == {"123", "456"}
    assert legacy.is_file() and custom.is_file()
    assert not Path(profiles.resolve_cache_path(second.id)).exists()


def test_subsonic_server_ids_never_fall_back_across_profiles(accounts):
    settings, profiles, _, _ = accounts
    first = profiles.create("subsonic", "First server")
    second = profiles.create("subsonic", "Second server")
    write_cache(profiles.resolve_cache_path(first.id), {KEY: "123"}, manual=[KEY])
    assert profiles.cache_fallback_files(second.id) == ()
    assert ResolveCacheStore(settings, profiles=profiles).entries(second.id)["total"] == 0


@pytest.mark.parametrize("flow", ["transfer", "sync", "import"])
def test_manual_deezer_match_is_reused_without_search_in_every_matching_flow(accounts, monkeypatch, tmp_path, flow):
    settings, profiles, first, second = accounts
    write_cache(profiles.resolve_cache_path(first.id), {KEY: "123"}, manual=[KEY])
    write_cache(profiles.resolve_cache_path(second.id), {KEY: None})
    raw = DeezerTarget.__new__(DeezerTarget)
    raw.cache_file = profiles.resolve_cache_path(second.id)
    raw._web = None
    raw._catalog_get = lambda *a, **kw: pytest.fail("A shared manual match must not search")
    raw.prefetch = lambda *a, **kw: None
    current, writes = [], []
    raw.playlist_tracks = lambda playlist: list(current)
    raw.list_playlists = lambda: {"mix": {"id": "destination", "title": "Mix"}}
    raw.is_editable = lambda playlist: True
    def add(playlist, ids):
        writes.extend(ids)
        current.extend({**TRACK, "id": identifier} for identifier in ids)
    raw.add = add
    raw.fetch_track = lambda identifier: {**TRACK, "id": identifier}
    dest = targets.AccountBoundTarget(raw, second, profiles)
    cache = load_target_cache(dest)
    conn = archive.connect(str(tmp_path / "songs.db"))
    source = MirrorTarget()
    source.source = "spotify"
    source.playlist_tracks = lambda playlist: [TRACK]
    try:
        if flow == "transfer":
            result = transfer(source, dest, {}, {}, cache, execute=True, max_adds=10)
            assert result["added"] == 1 and result["not_found"] == []
            assert transfer(source, dest, {}, {}, cache, execute=True, max_adds=10)["added"] == 0
        elif flow == "sync":
            opts = parse_args([])
            opts.execute, opts.max_removals = True, 0
            # An old archive link must not bypass the sibling manual choice.
            archive.set_links(conn, dest.source, {TRACK["id"]: "999"})
            result = runner.run_target(dest, [{"id": "source-playlist", "name": "Mix"}],
                lambda resource: [{**_normalize(TRACK, "spotify"), "id": TRACK["id"]}],
                conn, opts, source=source)
            assert result["added"] == 1 and result["missing"] == 0
        else:
            result = ImportMatcher(dest, cache).match_track({"title": TRACK["name"], "artist": "Queen"})
            assert result.status == "exact" and result.best.target_id == "123"
        assert writes == ([] if flow == "import" else ["123"])
    finally:
        conn.close()
