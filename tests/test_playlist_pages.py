"""Provider-native playlist pages used by the progressive web inspector."""


class _Response:
    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


def _spotify_item(track_id):
    return {
        "added_at": "2026-08-25T00:00:00Z",
        "track": {
            "id": track_id,
            "type": "track",
            "name": f"Track {track_id}",
            "artists": [{"name": "Artist"}],
            "album": {"name": "Album", "images": []},
            "duration_ms": 123_000,
            "external_ids": {"isrc": f"ISRC{track_id}"},
        },
    }


def _spotify_cookie_item(track_id):
    return {
        "itemV2": {
            "data": {
                "uri": f"spotify:track:{track_id}",
                "name": f"Track {track_id}",
                "artists": {"items": [{"profile": {"name": "Artist"}}]},
                "albumOfTrack": {"name": "Album", "coverArt": {"sources": []}},
                "trackDuration": {"totalMilliseconds": 123_000},
            }
        },
        "addedAt": {"isoString": "2026-08-25T00:00:00Z"},
    }


def test_spotify_oauth_playlist_page_uses_one_twenty_track_request():
    from songmirror.engine import spotify

    calls = []

    class Client:
        def playlist_items(self, playlist_id, **params):
            calls.append((playlist_id, params))
            return {
                "items": [_spotify_item("21"), _spotify_item("22")],
                "next": "https://api.spotify.com/v1/playlists/p/tracks?offset=22",
                "total": 42,
            }

    tracks, cursor = spotify.playlist_tracks_page(Client(), "playlist", cursor="20")

    assert [track["id"] for track in tracks] == ["21", "22"]
    assert cursor == "22"
    assert calls == [("playlist", {
        "market": "from_token",
        "additional_types": ("track",),
        "limit": 20,
        "offset": 20,
    })]


def test_spotify_cookie_playlist_page_uses_one_twenty_track_request(monkeypatch):
    from songmirror.engine import spotify_cookie

    calls = []

    def pathfinder(_operation, variables):
        calls.append(variables)
        return {
            "playlistV2": {
                "content": {
                    "items": [_spotify_cookie_item("21"), _spotify_cookie_item("22")],
                    "totalCount": 42,
                }
            }
        }

    monkeypatch.setattr(spotify_cookie, "_pf", pathfinder)

    tracks, cursor = spotify_cookie.playlist_tracks_page("playlist", cursor="20")

    assert [track["id"] for track in tracks] == ["21", "22"]
    assert cursor == "22"
    assert calls == [{"uri": "spotify:playlist:playlist", "offset": 20, "limit": 20}]


def test_qobuz_playlist_page_advances_by_raw_rows():
    from songmirror.engine.targets.qobuz import QobuzTarget

    target = QobuzTarget.__new__(QobuzTarget)
    calls = []

    def request(method, endpoint, params=None):
        calls.append((method, endpoint, params))
        return {
            "tracks": {
                "items": [{"id": 21, "title": "Twenty one", "performer": {"name": "Artist"}}],
                "total": 41,
            }
        }

    target._request = request
    tracks, cursor = target.playlist_tracks_page({"id": "playlist"}, cursor="20")

    assert tracks[0]["id"] == "21"
    assert cursor == "21"
    assert calls == [("GET", "playlist/get", {
        "playlist_id": "playlist",
        "extra": "tracks",
        "limit": 20,
        "offset": 20,
    })]


def test_deezer_rest_playlist_page_keeps_tokens_private_and_uses_index():
    from songmirror.engine.targets.deezer import DeezerTarget

    target = DeezerTarget.__new__(DeezerTarget)
    target._web = None
    calls = []

    def request(method, path, params=None):
        calls.append((method, path, params))
        return {
            "data": [{"id": 21, "title": "Twenty one", "artist": {"name": "Artist"}}],
            "total": 41,
            "next": "https://api.deezer.com/playlist/p/tracks?access_token=secret&index=21&limit=20",
        }

    target._request = request
    tracks, cursor = target.playlist_tracks_page({"id": "playlist"}, cursor="20")

    assert tracks[0]["id"] == "21"
    assert cursor == "21"
    assert "secret" not in cursor
    assert calls == [("GET", "playlist/playlist/tracks", {"limit": 20, "index": 20})]


def test_deezer_web_playlist_page_passes_through_graphql_cursor():
    from songmirror.engine.targets.deezer import DeezerTarget

    class Web:
        def playlist_tracks_page(self, playlist_id, cursor=None, limit=20):
            assert (playlist_id, cursor, limit) == ("playlist", "page-1", 20)
            return ([{"id": "21", "title": "Twenty one", "artist": {"name": "Artist"}}], "page-2")

    target = DeezerTarget.__new__(DeezerTarget)
    target._web = Web()

    tracks, cursor = target.playlist_tracks_page({"id": "playlist"}, cursor="page-1")

    assert tracks[0]["id"] == "21"
    assert cursor == "page-2"


def test_amazon_web_playlist_page_uses_one_graphql_connection_page():
    from songmirror.engine.targets.amazon_music import AmazonMusicTarget

    target = AmazonMusicTarget.__new__(AmazonMusicTarget)
    target._web = object()
    calls = []

    def graphql(operation, query, variables):
        calls.append((operation, variables))
        return {
            "playlist": {
                "tracks": {
                    "edges": [{
                        "cursor": "0:entry-21",
                        "itemId": "entry-21",
                        "node": {"id": "ASIN21", "title": "Twenty one"},
                    }],
                    "pageInfo": {"hasNextPage": True, "token": "page-2"},
                }
            }
        }

    target._graphql = graphql
    tracks, cursor = target.playlist_tracks_page({"id": "playlist"}, cursor="page-1")

    assert tracks[0]["id"] == "ASIN21"
    assert tracks[0]["relationship_id"] == "entry-21"
    assert cursor == "page-2"
    assert calls == [("SongMirrorAmazonPlaylistTracks", {
        "id": "playlist",
        "cursor": "page-1",
        "limit": 20,
    })]


def test_amazon_rest_playlist_page_hydrates_only_that_page():
    from songmirror.engine.targets.amazon_music import AmazonMusicTarget

    target = AmazonMusicTarget.__new__(AmazonMusicTarget)
    target._web = None
    calls = []

    def request(method, path, params=None):
        calls.append((method, path, params))
        return {
            "data": {"playlist": {"tracks": {
                "edges": [{"cursor": "0:entry-21", "node": {"id": "ASIN21"}}],
                "pageInfo": {"hasNextPage": True, "token": "page-2"},
            }}}
        }

    target._request = request
    hydrated = []
    target._track_details = lambda ids: (hydrated.extend(ids), {
        "ASIN21": {"id": "ASIN21", "title": "Twenty one"}
    })[1]

    tracks, cursor = target.playlist_tracks_page({"id": "playlist"}, cursor="page-1")

    assert tracks[0]["id"] == "ASIN21"
    assert cursor == "page-2"
    assert hydrated == ["ASIN21"]
    assert calls == [("GET", "playlists/playlist/tracks", {"limit": 20, "cursor": "page-1"})]


def test_apple_playlist_page_uses_offset_and_reuses_meta_total():
    from songmirror.engine.targets.apple import AppleMusicTarget

    target = AppleMusicTarget.__new__(AppleMusicTarget)
    calls = []

    def request(method, url, params=None, ok404=False):
        calls.append((method, url, params, ok404))
        return _Response({
            "data": [{
                "id": "entry-21",
                "attributes": {
                    "name": "Twenty one",
                    "artistName": "Artist",
                    "playParams": {"catalogId": "catalog-21"},
                },
            }],
            "meta": {"total": 41},
            "next": "/next",
        })

    target._request = request
    playlist = {"id": "playlist", "attributes": {}}
    tracks, cursor = target.playlist_tracks_page(playlist, cursor="20")

    assert tracks[0]["catalog_id"] == "catalog-21"
    assert cursor == "21"
    assert target.playlist_count(playlist) == 41
    assert len(calls) == 1
    assert calls[0][2] == {"limit": 20, "offset": 20}


def test_youtube_oauth_playlist_page_uses_one_twenty_item_request():
    from songmirror.engine.targets.ytmusic import YTMusicTarget

    target = YTMusicTarget.__new__(YTMusicTarget)
    calls = []

    def request(method, path, params=None):
        calls.append((method, path, params))
        return _Response({
            "items": [{
                "id": "entry-21",
                "contentDetails": {"videoId": "video-21"},
                "snippet": {"title": "Twenty one", "videoOwnerChannelTitle": "Artist - Topic"},
            }],
            "nextPageToken": "page-2",
        })

    target._request = request
    tracks, cursor = target.playlist_tracks_page({"playlistId": "playlist"}, cursor="page-1")

    assert tracks[0]["videoId"] == "video-21"
    assert tracks[0]["artist"] == "Artist"
    assert cursor == "page-2"
    assert calls == [("GET", "playlistItems", {
        "part": "snippet,contentDetails",
        "playlistId": "playlist",
        "maxResults": 20,
        "pageToken": "page-1",
    })]


def test_youtube_oauth_terminal_first_page_does_not_treat_null_as_repeated():
    from songmirror.engine.targets.ytmusic import YTMusicTarget

    target = YTMusicTarget.__new__(YTMusicTarget)
    target._request = lambda *args, **kwargs: _Response({"items": []})

    tracks, cursor = target.playlist_tracks_page({"playlistId": "playlist"})

    assert tracks == []
    assert cursor is None


def test_youtube_browser_terminal_first_page_does_not_treat_null_as_repeated(monkeypatch):
    from ytmusicapi import continuations, navigation
    from ytmusicapi.parsers import playlists

    from songmirror.engine.targets.ytmusic import _youtubei_playlist_page

    items = [{"fixture": "track"}]

    def navigate(_node, path, _none_if_absent=False):
        if path[-1] == 0:
            return None
        if path[-1] == "sectionListRenderer":
            return {"contents": []}
        if path == ["contents", 0, "musicPlaylistShelfRenderer"]:
            return {"contents": items}
        raise AssertionError(path)

    monkeypatch.setattr(navigation, "nav", navigate)
    monkeypatch.setattr(continuations, "get_continuation_token", lambda _items: None)
    monkeypatch.setattr(
        playlists,
        "parse_playlist_items",
        lambda _items, is_collaborative=False: [{"videoId": "video-1"}],
    )
    api = type("Api", (), {"_send_request": lambda self, *args: {}})()

    tracks, cursor = _youtubei_playlist_page(api, "playlist")

    assert tracks == [{"videoId": "video-1"}]
    assert cursor is None


def test_youtube_browser_playlist_page_preserves_collaborative_context(monkeypatch):
    from ytmusicapi import continuations, navigation
    from ytmusicapi.parsers import playlists

    from songmirror.engine.targets.ytmusic import (
        _YOUTUBEI_COLLABORATIVE_CURSOR_PREFIX,
        _youtubei_playlist_page,
    )

    first_items = [{"fixture": "first"}]
    second_items = [{"fixture": "second"}]
    requests = []
    parsed_contexts = []

    class Api:
        def _send_request(self, *args):
            requests.append(args)
            return {"fixture": "continuation" if "continuation" in args[1] else "first"}

    def navigate(node, path, _none_if_absent=False):
        if node.get("fixture") == "continuation":
            assert path[-1] == "continuationItems"
            return second_items
        if path == [*navigation.TWO_COLUMN_RENDERER, *navigation.TAB_CONTENT,
                    *navigation.SECTION_LIST_ITEM]:
            return {"musicResponsiveHeaderRenderer": {"fixture": "header"}}
        if path == navigation.RESPONSIVE_HEADER:
            return {"fixture": "header"}
        if path == [*navigation.TWO_COLUMN_RENDERER, "secondaryContents", *navigation.SECTION]:
            return {"contents": [{"musicPlaylistShelfRenderer": {"contents": first_items}}]}
        if path == [*navigation.CONTENT, "musicPlaylistShelfRenderer"]:
            return {"contents": first_items}
        raise AssertionError(path)

    def parse_items(items, is_collaborative=False):
        parsed_contexts.append(is_collaborative)
        return [{"videoId": items[0]["fixture"]}]

    monkeypatch.setattr(navigation, "nav", navigate)
    monkeypatch.setattr(playlists, "parse_playlist_header_meta", lambda _header: {
        "collaborators": {"text": "by Artist and 1 other", "avatars": []}
    })
    monkeypatch.setattr(playlists, "parse_playlist_items", parse_items)
    monkeypatch.setattr(
        continuations,
        "get_continuation_token",
        lambda items: "native-page-2" if items is first_items else None,
    )

    first_tracks, cursor = _youtubei_playlist_page(Api(), "playlist")
    second_tracks, terminal_cursor = _youtubei_playlist_page(Api(), "playlist", cursor=cursor)

    assert first_tracks == [{"videoId": "first"}]
    assert second_tracks == [{"videoId": "second"}]
    assert cursor == f"{_YOUTUBEI_COLLABORATIVE_CURSOR_PREFIX}native-page-2"
    assert terminal_cursor is None
    assert parsed_contexts == [True, True]
    assert requests == [
        ("browse", {"browseId": "VLplaylist"}, ""),
        ("browse", {"continuation": "native-page-2"}),
    ]


def test_youtube_browser_audio_playlist_page_allows_missing_header(monkeypatch):
    from ytmusicapi import continuations, navigation
    from ytmusicapi.parsers import playlists

    from songmirror.engine.targets.ytmusic import _youtubei_playlist_page

    items = [{"fixture": "audio-track"}]
    requests = []

    class Api:
        def _send_request(self, *args):
            requests.append(args)
            return {"fixture": "audio"}

    def navigate(_node, path, _none_if_absent=False):
        if path == [*navigation.TWO_COLUMN_RENDERER, *navigation.TAB_CONTENT,
                    *navigation.SECTION_LIST_ITEM]:
            return None
        if path == [*navigation.TWO_COLUMN_RENDERER, "secondaryContents", *navigation.SECTION]:
            return {"contents": [{"musicPlaylistShelfRenderer": {"contents": items}}]}
        if path == [*navigation.CONTENT, "musicPlaylistShelfRenderer"]:
            return {"contents": items}
        raise AssertionError(path)

    def fail_header_parse(_header):
        raise AssertionError("audio playlists do not have a regular playlist header")

    monkeypatch.setattr(navigation, "nav", navigate)
    monkeypatch.setattr(playlists, "parse_playlist_header_meta", fail_header_parse)
    monkeypatch.setattr(
        playlists,
        "parse_playlist_items",
        lambda rows, is_collaborative=False: [{
            "videoId": rows[0]["fixture"],
            "is_collaborative": is_collaborative,
        }],
    )
    monkeypatch.setattr(continuations, "get_continuation_token", lambda _items: None)

    tracks, cursor = _youtubei_playlist_page(Api(), "OLAaudio")

    assert tracks == [{"videoId": "audio-track", "is_collaborative": False}]
    assert cursor is None
    assert requests == [("browse", {"browseId": "VLOLAaudio"}, "")]


def test_youtube_browser_playlist_page_returns_one_native_page(monkeypatch):
    from songmirror.engine.targets import ytmusic
    from songmirror.engine.targets.ytmusic import YTMusicBrowserTarget

    calls = []

    def read_page(api, playlist_id, cursor=None):
        calls.append((api, playlist_id, cursor))
        return ([{
            "videoId": "video-21",
            "setVideoId": "entry-21",
            "title": "Twenty one",
            "artists": [{"name": "Artist - Topic"}],
        }], "page-2")

    monkeypatch.setattr(ytmusic, "_youtubei_playlist_page", read_page)
    target = YTMusicBrowserTarget.__new__(YTMusicBrowserTarget)
    target._api = object()

    tracks, cursor = target.playlist_tracks_page({"playlistId": "playlist"}, cursor="page-1")

    assert tracks[0]["videoId"] == "video-21"
    assert tracks[0]["artist"] == "Artist"
    assert cursor == "page-2"
    assert calls == [(target._api, "playlist", "page-1")]
