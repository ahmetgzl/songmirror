"""Request pacing and failures observed against the live ListenBrainz API."""

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from songmirror import listenbrainz
from songmirror.engine.targets.base import TargetTransientError


class Clock:
    def __init__(self):
        self.now = 100.0
        self.waits = []

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(listenbrainz, "_REQUESTS", listenbrainz._RequestGate(
        clock=lambda: clock.now, sleep=clock.sleep,
    ))
    return clock


def response(*, status=200, headers=None):
    return SimpleNamespace(
        status_code=status, ok=status < 400, headers=headers or {},
        json=lambda: {"feedback": [], "offset": 0, "total_count": 0},
    )


def test_profiles_and_connection_checks_share_one_request_budget(clock):
    starts = []
    session = Mock()
    session.get.side_effect = lambda *args, **kwargs: starts.append(clock.now) or response()
    first = listenbrainz.ListenBrainz("first", session)
    second = listenbrainz.ListenBrainz("second", session)
    first.feedback_page(count=1)
    clock.now += 0.25
    second.feedback_page(count=1)
    first.loved_tracks()
    assert starts == [100, 101, 102]
    assert clock.waits == [0.75, 1]
    headers = session.get.call_args.kwargs["headers"]
    assert headers["User-Agent"] == listenbrainz.USER_AGENT
    assert "/0.1.0" in headers["User-Agent"]
    assert "https://github.com/ahnafnafee/songmirror/issues" in headers["User-Agent"]
    assert headers["Accept"] == "application/json"
    assert "Authorization" not in headers
    session.post.assert_not_called()


def test_slow_requests_need_no_additional_wait(clock):
    session = Mock()

    def get(*args, **kwargs):
        clock.now += 2
        return response()

    session.get.side_effect = get
    client = listenbrainz.ListenBrainz("listener", session)
    client.feedback_page()
    client.feedback_page()
    assert clock.waits == []


@pytest.mark.parametrize("status", [200, 429])
def test_server_rate_limit_blocks_other_profiles_until_reset(clock, status):
    session = Mock()
    session.get.side_effect = [response(status=status, headers={
        "X-RateLimit-Remaining": "0", "X-RateLimit-Reset-In": "10", "Retry-After": "5",
    }), response()]
    first = listenbrainz.ListenBrainz("first", session)
    if status == 429:
        with pytest.raises(TargetTransientError) as caught:
            first.feedback_page()
        assert caught.value.retry_after == 10
    else:
        assert first.loved_tracks() == []
    clock.now += 3
    with pytest.raises(TargetTransientError, match="retry in 7 seconds") as caught:
        listenbrainz.ListenBrainz("second", session).feedback_page()
    assert caught.value.retry_after == 7
    assert session.get.call_count == 1
    session.post.assert_not_called()
    clock.now += 7
    assert first.loved_tracks() == []
    assert session.get.call_count == 2


@pytest.mark.parametrize("headers", [
    {}, {"Retry-After": "invalid"}, {"Retry-After": "nan"},
    {"Retry-After": "inf"}, {"X-RateLimit-Reset-In": "-1"},
])
def test_rate_limit_without_a_valid_reset_uses_a_bounded_cooldown(clock, headers):
    session = Mock()
    session.get.return_value = response(status=429, headers=headers)
    with pytest.raises(TargetTransientError) as caught:
        listenbrainz.ListenBrainz("listener", session).loved_tracks()
    assert caught.value.retry_after == 30
    with pytest.raises(TargetTransientError):
        listenbrainz.ListenBrainz("other", session).loved_tracks()
    assert session.get.call_count == 1


def test_http_date_retry_after_is_supported():
    future = datetime.now(timezone.utc) + timedelta(seconds=90)
    delay = listenbrainz._retry_delay({"Retry-After": format_datetime(future)})
    assert 88 < delay <= 90


def test_verification_on_both_routes_is_a_failure_never_an_empty_library(clock):
    session = Mock()
    result = response(headers={"Content-Type": "text/html; charset=utf-8"})
    result.json = Mock(side_effect=ValueError("not JSON"))
    session.get.return_value = result
    session.post.return_value = result
    with pytest.raises(TargetTransientError, match="web page or error instead of recording data"):
        listenbrainz.ListenBrainz("listener", session).loved_tracks()
    result.json.assert_not_called()


def website_response(body, *, status=200):
    result = response(status=status, headers={"Content-Type": "application/json"})
    result.json = lambda: body
    return result


def feedback(identifier="recording", **overrides):
    return {"recording_mbid": identifier, "score": 1, "created": 1_700_000_000,
            "track_metadata": {"track_name": "Song", "artist_name": "Artist"}, **overrides}


def test_html_api_falls_back_to_the_observed_public_website_read(clock):
    rows = [feedback("first"), feedback("second")]
    session = Mock()
    session.get.return_value = response(headers={"Content-Type": "text/html"})
    session.post.return_value = website_response({
        "user": {"name": "A User"}, "feedback_count": 2, "feedback": rows,
    })
    actual, total = listenbrainz.ListenBrainz("a user", session).feedback_page(offset=1, count=1)
    assert actual == rows[1:] and total == 2
    assert clock.waits == [1]
    args, kwargs = session.post.call_args
    assert args == ("https://listenbrainz.org/user/a%20user/taste/",)
    assert kwargs["params"] == {"score": 1}
    assert kwargs["allow_redirects"] is False
    assert kwargs["headers"]["User-Agent"] == listenbrainz.USER_AGENT
    assert "Authorization" not in kwargs["headers"]
    assert "data" not in kwargs and "json" not in kwargs


@pytest.mark.parametrize("rows", [[], [feedback()], [feedback(track_metadata=None)]])
def test_complete_website_loves_retain_identities_and_missing_metadata(clock, rows):
    session = Mock()
    session.get.return_value = response(headers={"Content-Type": "text/html"})
    session.post.return_value = website_response({
        "user": {"name": "listener"}, "feedback_count": len(rows), "feedback": rows,
    })
    tracks = listenbrainz.ListenBrainz("listener", session).loved_tracks()
    assert len(tracks) == len(rows)
    if rows:
        assert tracks[0]["id"] == "mbid:recording"
        assert tracks[0]["_metadata_incomplete"] == (rows[0]["track_metadata"] is None)


@pytest.mark.parametrize("body", [
    {}, {"user": {"name": "other"}, "feedback_count": 0, "feedback": []},
    {"user": {"name": "listener"}, "feedback_count": -1, "feedback": []},
    {"user": {"name": "listener"}, "feedback_count": 1, "feedback": [feedback(score=-1)]},
    {"user": {"name": "listener"}, "feedback_count": 1, "feedback": {}},
])
def test_invalid_or_wrong_user_website_data_is_rejected(clock, body):
    session = Mock()
    session.get.return_value = response(headers={"Content-Type": "text/html"})
    session.post.return_value = website_response(body)
    with pytest.raises(TargetTransientError, match="incomplete website data"):
        listenbrainz.ListenBrainz("listener", session).loved_tracks()


def test_website_first_25_entries_cannot_replace_a_larger_collection(clock):
    session = Mock()
    session.get.return_value = response(headers={"Content-Type": "text/html"})
    session.post.return_value = website_response({
        "user": {"name": "listener"}, "feedback_count": 26,
        "feedback": [feedback(str(i)) for i in range(25)],
    })
    with pytest.raises(TargetTransientError, match="25 of 26 loved recordings"):
        listenbrainz.ListenBrainz("listener", session).loved_tracks()


@pytest.mark.parametrize("status", [302, 404, 503])
def test_failed_or_redirected_website_read_never_becomes_empty(clock, status):
    session = Mock()
    session.get.return_value = response(headers={"Content-Type": "text/html"})
    session.post.return_value = website_response({}, status=status)
    with pytest.raises(RuntimeError):
        listenbrainz.ListenBrainz("listener", session).loved_tracks()
    assert session.post.call_count == 1
