"""Public/private visibility requested by the explicit playlist-creation flows."""

import pytest

from songmirror.engine.targets import TargetCapabilityError


class _Json:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _no_sleep(monkeypatch, *modules):
    for module in modules:
        monkeypatch.setattr(module, "polite_sleep", lambda *_: None)


def _spotify_oauth(monkeypatch, sent):
    import songmirror.engine.targets.spotify_target as module

    class Client:
        def current_user(self):
            return {"id": "me"}

        def user_playlist_create(self, user, name, public, description):
            sent.append(public)
            return {"id": "p1", "name": name}

    monkeypatch.setenv("SPOTIFY_WRITE_BACKEND", "oauth")
    _no_sleep(monkeypatch, module)
    return module.SpotifyTarget(Client(), "unused.json")


def _deezer_web(monkeypatch, sent):
    import songmirror.engine.targets.deezer as module
    from songmirror.deezer_web import DeezerWebClient

    client = DeezerWebClient.__new__(DeezerWebClient)

    def execute(operation, query, variables=None, mutation=False):
        sent.append(not variables["input"]["isPrivate"])
        return {"createPlaylist": {"playlist": {"id": "p1", "title": "Mix"}}}

    client.execute = execute
    _no_sleep(monkeypatch, module)
    monkeypatch.setenv("DEEZER_REFRESH_TOKEN", "refresh")  # what selects the web session
    target = module.DeezerTarget.__new__(module.DeezerTarget)
    target._web = client
    return target


def _qobuz(monkeypatch, sent):
    import songmirror.engine.targets.qobuz as module

    target = module.QobuzTarget.__new__(module.QobuzTarget)

    def request(method, endpoint, *, params=None):
        sent.append(params["is_public"] == "true")
        return {"id": 1, "name": params["name"]}

    target._request = request
    _no_sleep(monkeypatch, module)
    return target


def _tidal(monkeypatch, sent):
    import songmirror.engine.targets.tidal as module

    target = module.TidalTarget.__new__(module.TidalTarget)

    def request(method, path, *, params=None, json_body=None):
        sent.append(json_body["data"]["attributes"]["accessType"] == "PUBLIC")
        return _Json({"data": {"id": "p1", "attributes": {"name": "Mix"}}})

    target._request = request
    _no_sleep(monkeypatch, module)
    return target


def _amazon_web(monkeypatch, sent):
    import songmirror.engine.targets.amazon_music as module

    class Web:
        def execute(self, operation, query, variables=None, mutation=False):
            sent.append(variables["visibility"] == "PUBLIC")
            return {"createPlaylist": {"id": "p1", "title": "Mix"}}

    target = module.AmazonMusicTarget.__new__(module.AmazonMusicTarget)
    target._web = Web()
    _no_sleep(monkeypatch, module)
    return target


def _amazon_api(monkeypatch, sent):
    import songmirror.engine.targets.amazon_music as module

    target = module.AmazonMusicTarget.__new__(module.AmazonMusicTarget)
    target._web = None

    def request(method, path, *, params=None, json_body=None):
        sent.append(json_body["visibility"] == "PUBLIC")
        return {"data": {"createPlaylist": {"id": "p1", "title": "Mix"}}}

    target._request = request
    _no_sleep(monkeypatch, module)
    return target


def _youtube_data_api(monkeypatch, sent):
    import songmirror.engine.targets.ytmusic as module

    target = module.YTMusicTarget.__new__(module.YTMusicTarget)

    def request(method, path, *, params=None, json_body=None, ok404=False):
        sent.append(json_body["status"]["privacyStatus"] == "public")
        return _Json({"id": "PL1"})

    target._request = request
    _no_sleep(monkeypatch, module)
    return target


def _youtube_browser(monkeypatch, sent):
    import songmirror.engine.targets.ytmusic as module

    class Api:
        def create_playlist(self, name, description, privacy_status):
            sent.append(privacy_status == "PUBLIC")
            return "PL1"

    target = module.YTMusicBrowserTarget.__new__(module.YTMusicBrowserTarget)
    target._api = Api()
    _no_sleep(monkeypatch, module)
    return target


PUBLIC_CAPABLE = [
    _spotify_oauth, _deezer_web, _qobuz, _tidal, _amazon_web, _amazon_api,
    _youtube_data_api, _youtube_browser,
]


@pytest.mark.parametrize("build", PUBLIC_CAPABLE, ids=lambda build: build.__name__.strip("_"))
def test_explicit_create_requests_a_public_playlist_and_defaults_to_private(monkeypatch, build):
    sent = []
    target = build(monkeypatch, sent)

    target.create({"name": "Mix", "description": "d", "_create_public": True})
    target.create({"name": "Mix", "description": "d"})
    # A sync mirrors a raw source playlist; that playlist's own sharing flag
    # must never make the mirror public.
    target.create({"name": "Mix", "description": "d", "public": True, "_public": True})

    assert sent == [True, False, False]
    assert type(target).creates_public_playlists() is True


def _spotify_cookie(monkeypatch, sent):
    import songmirror.engine.spotify_cookie as cookie
    import songmirror.engine.targets.spotify_target as module

    monkeypatch.setenv("SPOTIFY_WRITE_BACKEND", "cookie")
    monkeypatch.setattr(cookie, "create", lambda *args, **kwargs: sent.append(args))
    _no_sleep(monkeypatch, module)
    return module.SpotifyTarget(None, "unused.json")


def _deezer_api(monkeypatch, sent):
    import songmirror.engine.targets.deezer as module

    monkeypatch.delenv("DEEZER_WEB_HEADERS", raising=False)
    monkeypatch.delenv("DEEZER_REFRESH_TOKEN", raising=False)
    target = module.DeezerTarget.__new__(module.DeezerTarget)
    target._web = None
    target._request = lambda *args, **kwargs: sent.append(args)
    return target


def _apple(monkeypatch, sent):
    import songmirror.engine.targets.apple as module

    target = module.AppleMusicTarget.__new__(module.AppleMusicTarget)
    target._request = lambda *args, **kwargs: sent.append(args)
    return target


@pytest.mark.parametrize(
    "build", [_spotify_cookie, _deezer_api, _apple], ids=lambda build: build.__name__.strip("_"),
)
def test_a_public_request_fails_closed_where_the_account_cannot_honor_it(monkeypatch, build):
    sent = []
    target = build(monkeypatch, sent)

    with pytest.raises(TargetCapabilityError, match="public"):
        target.create({"name": "Mix", "description": "d", "_create_public": True})

    assert sent == []
    assert type(target).creates_public_playlists() is False


def test_deezer_public_capability_follows_the_configured_session(monkeypatch):
    from songmirror.engine.targets.deezer import DeezerTarget

    monkeypatch.delenv("DEEZER_WEB_HEADERS", raising=False)
    monkeypatch.delenv("DEEZER_REFRESH_TOKEN", raising=False)
    assert DeezerTarget.creates_public_playlists() is False
    monkeypatch.setenv("DEEZER_REFRESH_TOKEN", "refresh")
    assert DeezerTarget.creates_public_playlists() is True
