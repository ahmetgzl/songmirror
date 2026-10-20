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


def test_browser_verification_page_is_a_transient_failure_never_an_empty_library(clock):
    session = Mock()
    result = response(headers={"Content-Type": "text/html; charset=utf-8"})
    result.json = Mock(side_effect=ValueError("not JSON"))
    session.get.return_value = result
    with pytest.raises(TargetTransientError, match="web page instead of recording data"):
        listenbrainz.ListenBrainz("listener", session).loved_tracks()
    result.json.assert_not_called()
