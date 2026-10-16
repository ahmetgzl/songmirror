"""Recoverable YouTube playlist-creation responses stay actionable and private."""

import asyncio

import pytest

from songmirror.engine.targets import AccountBoundTarget, ytmusic
from songmirror.engine.targets.base import TargetAuthError
from songmirror.services.account_profiles import AccountProfileStore
from songmirror.services.events import EventBus
from songmirror.services.settings import SettingsStore
from songmirror.services.sync_service import SyncService
from songmirror.services.transfers import TransferService


@pytest.mark.parametrize("public", [False, True], ids=["private", "public"])
def test_youtube_account_transfer_explains_channel_setup_then_can_be_retried(
    monkeypatch, tmp_path, public,
):
    # The failed live create returned this action instead of a playlist id.
    # Tracking data and the channel-creation token must not become UI text.
    response = {
        "responseContext": {"tracking": "private-tracking-value"},
        "actions": [{"channelCreationFormEndpoint": {
            "channelCreationToken": "private-channel-token",
            "source": "CREATE_PLAYLIST_PRIVACY_DROPDOWN_CHANNEL_CREATION_SOURCE",
        }}],
    }
    created, writes = [], []
    monkeypatch.setattr(ytmusic, "polite_sleep", lambda *_: None)
    settings = SettingsStore(dir=tmp_path)
    profiles = AccountProfileStore(settings)
    old_account = profiles.create("ytmusic", "Old account")
    new_account = profiles.create("ytmusic", "New account")

    class Api:
        channel_ready = False

        def get_library_playlists(self, limit):
            return [{"playlistId": "old-playlist", "title": "Old playlist"}]

        def get_playlist(self, playlist_id, limit):
            return {"title": "Old playlist", "tracks": [{
                "videoId": "video-1", "title": "Song", "artists": [{"name": "Artist"}],
                "videoType": "MUSIC_VIDEO_TYPE_ATV",
            }] if playlist_id == "old-playlist" else []}

        def create_playlist(self, name, description, privacy_status):
            created.append(privacy_status)
            return "new-playlist" if self.channel_ready else response

        def add_playlist_items(self, playlist_id, video_ids, duplicates):
            writes.append((playlist_id, list(video_ids)))
            return {"status": "STATUS_SUCCEEDED"}

    api = Api()
    targets = {}
    for account in (old_account, new_account):
        target = ytmusic.YTMusicBrowserTarget.__new__(ytmusic.YTMusicBrowserTarget)
        target.cache_file = str(tmp_path / f"{account.id}.json")
        target._api = api
        targets[account.id] = AccountBoundTarget(target, account, profiles)
    monkeypatch.setattr(TransferService, "_build", lambda _self, account, _opts: targets[account])

    async def scenario():
        bus = EventBus()
        bus.bind_loop(asyncio.get_running_loop())
        service = TransferService(settings, bus, SyncService(settings, bus), profiles)
        spec = {
            "source_account": old_account.id, "source_playlist_id": "old-playlist",
            "dest_account": new_account.id, "dest_playlist_id": None,
            "dest_name": "Copy", "dest_public": public,
        }

        async def copy():
            job = service.submit(spec)
            for _ in range(100):
                if job["status"] in {"done", "error"}:
                    return service.public(job)
                await asyncio.sleep(0.01)
            pytest.fail("Transfer did not finish")

        failed = await copy()
        assert failed["status"] == "error"
        assert failed["error"].startswith("YouTube requires a channel")
        assert "destination account" in failed["error"]
        assert "retry" in failed["error"].lower()
        assert "channelCreation" not in failed["error"]
        assert "private-tracking-value" not in failed["error"]
        assert "private-channel-token" not in failed["error"]
        assert failed["added"] == 0
        assert failed["dest"]["playlist_id"] is None
        assert writes == []
        # Creation is not silently retried with different privacy settings.
        assert created == ["PUBLIC" if public else "PRIVATE"]

        # Completing setup externally permits a fresh copy using the same
        # connected profiles; no account or source-playlist swap is needed.
        api.channel_ready = True
        completed = await copy()
        assert completed["status"] == "done", completed["error"]
        assert completed["dest"]["playlist_id"] == "new-playlist"
        assert completed["added"] == 1
        assert writes == [("new-playlist", ["video-1"])]
        assert created == ["PUBLIC" if public else "PRIVATE"] * 2

    asyncio.run(scenario())


@pytest.mark.parametrize("response", [
    None, "", "   ",
    {"actions": None, "token": "private-response-token"},
    {"actions": {"unexpected": "shape"}, "token": "private-response-token"},
    {"actions": [None, "unexpected"], "token": "private-response-token"},
])
def test_unconfirmed_creation_does_not_expose_response_or_return_a_playlist(response):
    target = ytmusic.YTMusicBrowserTarget.__new__(ytmusic.YTMusicBrowserTarget)
    target._api = type("Api", (), {"create_playlist": lambda *_args, **_kwargs: response})()

    with pytest.raises(TargetAuthError, match="did not confirm playlist creation") as caught:
        target.create({"name": "Copy"})

    assert "private-response-token" not in str(caught.value)
    assert "retry" in str(caught.value)
