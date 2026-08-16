"""The optional logs event sink feeds the live view."""

from songmirror.engine import logs


def test_sink_receives_typed_events():
    seen = []
    logs.set_sink(seen.append)
    try:
        logs.log_add("X - Y", tag="apple")
        logs.log_remove("A - B", tag="yt", dry=True)
        logs.log_miss("Z - W", tag="apple")
    finally:
        logs.set_sink(None)
    by_kind = {(e.kind, e.tag): e for e in seen}
    assert ("add", "apple") in by_kind
    assert ("remove", "yt") in by_kind
    assert by_kind[("remove", "yt")].data == {"dry": True}
    assert by_kind[("add", "apple")].message == "X - Y"  # raw, unpainted


def test_sink_none_is_noop():
    logs.set_sink(None)
    logs.log_note("hi")  # must not raise


def test_protection_and_identity_repair_events_keep_structured_evidence():
    seen = []
    logs.set_sink(seen.append)
    try:
        logs.log_protected(
            "protected four changes", tag="qobuz",
            data={"count": 4, "classification": "unconfirmed_absence", "provider": "qobuz"},
        )
        logs.log_repair(
            "repaired two identities", tag="spotify",
            data={"count": 2, "classification": "identity_migration", "provider": "spotify"},
        )
    finally:
        logs.set_sink(None)

    assert [(event.kind, event.tag, event.data) for event in seen] == [
        ("hold", "qobuz", {"count": 4, "classification": "unconfirmed_absence", "provider": "qobuz"}),
        ("repair", "spotify", {"count": 2, "classification": "identity_migration", "provider": "spotify"}),
    ]


def test_broken_sink_never_breaks_logging():
    def boom(_):
        raise RuntimeError("nope")
    logs.set_sink(boom)
    try:
        logs.log_add("still fine", tag="sync")  # must not propagate
    finally:
        logs.set_sink(None)
