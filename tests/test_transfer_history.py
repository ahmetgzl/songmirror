"""Durable transfer audit records, including failures and interrupted work."""

import pytest

from songmirror.services.transfer_history import TransferHistory


def job(id="one", status="queued", created_at="2026-10-15T10:00:00+00:00"):
    return {"id": id, "status": status, "created_at": created_at,
            "source": {"account": "old", "provider": "ytmusic", "name": "Old email",
                       "playlist_id": "source", "playlist_name": "Music 100%"},
            "dest": {"account": "new", "provider": "ytmusic", "name": "New email",
                     "playlist_id": "destination", "playlist_name": "Copy"},
            "added": 3, "total": 10, "processed": 10, "conflicts": [], "error": None,
            "_spec": {"secret": "never store"}, "unexpected_secret": "never store"}


def test_history_survives_reopen_and_records_failure_after_partial_writes(tmp_path):
    store = TransferHistory(tmp_path)
    record = job()
    store.save(record)
    record.update(status="running", phase="copying", added=5)
    store.save(record)
    record.update(status="error", error="Provider unavailable", added=6)
    store.save(record)
    reopened = TransferHistory(tmp_path)
    result = reopened.get("one")
    assert result["added"] == 6
    assert result["error"] == "Provider unavailable"
    assert "_spec" not in result and "unexpected_secret" not in result
    assert [e["status"] for e in result["events"]] == ["queued", "running", "error"]
    assert reopened.list()["total"] == 1
    assert b"never store" not in (tmp_path / "transfer_history.db").read_bytes()


def test_history_filters_either_endpoint_literal_search_dates_and_pagination(tmp_path):
    store = TransferHistory(tmp_path)
    store.save(job("a", "done"))
    store.save(job("b", "error", "2026-10-16T10:00:00+00:00"))
    other = job("c", "stopped")
    other["source"].update(account="another", provider="spotify", playlist_name="Other")
    other["dest"].update(account="third", provider="apple")
    store.save(other)
    assert [j["id"] for j in store.list(status="error")["items"]] == ["b"]
    assert store.list(account="new", provider="ytmusic", search="100%古")["total"] == 0
    assert store.list(search="_")["total"] == 0
    assert store.list(account="new", provider="ytmusic", search="100%")["total"] == 2
    assert store.list(search="' OR 1=1 --")["total"] == 0
    assert store.list(after="2026-10-16T00:00:00+00:00")["total"] == 1
    assert store.list(before="2026-10-16T00:00:00+00:00")["total"] == 2
    assert [j["id"] for j in store.list(limit=1, offset=1)["items"]] == ["c"]
    assert len(store.list()["accounts"]) == 4
    assert "events" not in store.list()["items"][0]


@pytest.mark.parametrize("status", ["queued", "running", "paused", "busy"])
def test_restart_marks_unfinished_records_failed_without_losing_counts(tmp_path, status):
    store = TransferHistory(tmp_path)
    store.save(job(status=status))
    store.save(job("done", "done"))
    store.recover()
    recovered = store.get("one")
    assert recovered["status"] == "error"
    assert recovered["added"] == 3
    assert recovered["finished_at"]
    assert "restart" in recovered["error"]
    assert recovered["events"][-1]["kind"] == "interrupted"
    assert store.get("done")["status"] == "done"
    store.recover()
    assert len(store.get("one")["events"]) == 2


def test_service_persists_client_setup_failures_and_excludes_private_runtime(tmp_path, monkeypatch):
    import asyncio
    from songmirror.services.events import EventBus
    from songmirror.services.settings import SettingsStore
    from songmirror.services.transfers import TransferService
    from songmirror.services.sync_service import SyncService

    settings, bus = SettingsStore(dir=tmp_path), EventBus()
    service = TransferService(settings, bus, SyncService(settings, bus))

    def fail(*_args):
        raise RuntimeError("Account could not connect")

    monkeypatch.setattr(service, "_build", fail)

    async def scenario():
        submitted = service.submit({"source_provider": "ytmusic", "dest_provider": "ytmusic",
                                    "source_playlist_id": "source", "dest_name": "Copy"})
        for _ in range(100):
            if submitted["status"] == "error":
                break
            await asyncio.sleep(0.01)
        return submitted["id"]

    id = asyncio.run(scenario())
    saved = service.history.get(id)
    assert saved["status"] == "error" and "Account could not connect" in saved["error"]
    assert saved["created_at"] <= saved["started_at"] <= saved["finished_at"]
    assert [e["status"] for e in saved["events"]] == ["queued", "running", "error"]
    reopened = TransferService(SettingsStore(dir=tmp_path), EventBus(), None)
    assert reopened.get(id)["historical"] is True
    assert reopened.list_active() == []
    assert reopened.resume(id) is False and reopened.apply_resolved(id) is False


def test_history_routes_validate_filters_and_survive_application_restart(tmp_path):
    from fastapi.testclient import TestClient
    from songmirror.services.settings import SettingsStore
    from songmirror.web import create_app

    app = create_app(settings=SettingsStore(dir=tmp_path))
    app.state.transfers.history.save(job(status="running"))
    with TestClient(app) as client:
        result = client.get("/api/transfers/history", params={"status": "error", "account": "new",
                            "after": "2026-10-15T05:00:00-04:00", "before": "2026-10-16"})
        assert result.status_code == 200
        assert result.json()["total"] == 1
        assert client.get("/api/transfers").json() == []
        detail = client.get("/api/transfers/history/one").json()
        assert detail["events"][-1]["kind"] == "interrupted"
        assert client.get("/api/transfers/one").json()["historical"] is True
        assert client.post("/api/transfers/one/resume").json() == {"ok": False}
        assert client.get("/api/transfers/history/missing").status_code == 404
        for params in ({"limit": 101}, {"offset": -1}, {"status": "unknown"}, {"after": "invalid"},
                       {"after": "2026-10-16", "before": "2026-10-15"}):
            assert client.get("/api/transfers/history", params=params).status_code == 422


def test_parallel_progress_saves_preserve_audit_and_latest_snapshot(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    store = TransferHistory(tmp_path)
    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(lambda n: store.save(job(str(n), "done")), range(30)))
    reopened = TransferHistory(tmp_path)
    assert reopened.list(limit=100)["total"] == 30
    assert all(len(reopened.get(str(n))["events"]) == 1 for n in range(30))
