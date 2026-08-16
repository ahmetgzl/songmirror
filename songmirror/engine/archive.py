"""Ever-growing local SQLite archive + resolution memory.

The main tables in one file:
- songs:      every track ever seen on any service (never deleted) — a durable
              metadata record with first/last-seen timestamps.
- links:      spotify_id -> target_id for every successful match, so later
              passes match by hard identifier instead of re-searching.
- sync_state: a playlist's Spotify snapshot_id after a clean pass, so an
              unchanged pair can be skipped wholesale.

SQLite over a pickle blob: incremental writes, crash-safe, and inspectable
(`sqlite3 song_cache.db "SELECT name, artist, last_seen FROM songs"`).
"""

import json
import sqlite3
from datetime import datetime, timezone

SCHEMAS = [
    """
CREATE TABLE IF NOT EXISTS songs (
    source      TEXT NOT NULL,
    id          TEXT NOT NULL,
    isrc        TEXT,
    name        TEXT,
    artist      TEXT,
    album       TEXT,
    duration_ms INTEGER,
    meta        TEXT,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    PRIMARY KEY (source, id)
)
""",
    """
CREATE TABLE IF NOT EXISTS links (
    spotify_id TEXT NOT NULL,
    target     TEXT NOT NULL,
    target_id  TEXT NOT NULL,
    updated    TEXT NOT NULL,
    PRIMARY KEY (spotify_id, target)
)
""",
    """
CREATE TABLE IF NOT EXISTS sync_state (
    pair         TEXT NOT NULL,
    target       TEXT NOT NULL,
    snapshot_id  TEXT,
    target_count INTEGER,
    updated      TEXT NOT NULL,
    PRIMARY KEY (pair, target)
)
""",
    # N-way sync: the canonical membership of a logical playlist ON EACH PROVIDER
    # after the last clean pass. Per-provider (not one shared set) is essential:
    # a track absent from a provider's own prior membership is never a removal
    # there, so a track that simply can't be matched on that service is not
    # mistaken for a user deletion. See targets/base.py.
    """
CREATE TABLE IF NOT EXISTS playlist_state (
    playlist     TEXT NOT NULL,
    source       TEXT NOT NULL,
    canonical_id TEXT NOT NULL,
    PRIMARY KEY (playlist, source, canonical_id)
)
""",
    # Membership rows alone cannot distinguish "never initialized" from an
    # initialized empty playlist. N-way merge semantics need that distinction:
    # a newly connected peer contributes bootstrap state, while an established
    # empty peer can contribute a real deletion. Keep the marker separately so
    # an empty canonical set remains representable.
    """
CREATE TABLE IF NOT EXISTS playlist_state_meta (
    playlist       TEXT NOT NULL,
    source         TEXT NOT NULL,
    initialized_at TEXT NOT NULL,
    PRIMARY KEY (playlist, source)
)
""",
    # One trusted executing pass has observed this established baseline member
    # missing from its provider. N-way reconcile requires the same absence on a
    # second trusted pass before it may propagate a deletion elsewhere.
    """
CREATE TABLE IF NOT EXISTS playlist_pending_removal (
    playlist      TEXT NOT NULL,
    source        TEXT NOT NULL,
    canonical_id  TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    PRIMARY KEY (playlist, source, canonical_id)
)
""",
    # The last HARD canonical id (ISRC / Spotify-linked) a provider's track
    # resolved to. Provider metadata is mutable: YouTube's youtubei read
    # alternates between a track's artist and its auto-generated channel for the
    # same video, and a re-keyed entry is indistinguishable from a deletion. So
    # once a physical entry has earned a hard identity it keeps it, even when a
    # later read is too degraded to derive one. See targets/base.py.
    """
CREATE TABLE IF NOT EXISTS track_identity (
    source       TEXT NOT NULL,
    track_id     TEXT NOT NULL,
    canonical_id TEXT NOT NULL,
    updated      TEXT NOT NULL,
    PRIMARY KEY (source, track_id)
)
""",
    # Every hard identity a stable provider track has held. The current value in
    # track_identity is global to the provider track, while playlist baselines
    # are scoped per playlist; retaining transition history lets each playlist
    # repair OLD -> NEW when it is reconciled, instead of only the first one.
    """
CREATE TABLE IF NOT EXISTS track_identity_history (
    source       TEXT NOT NULL,
    track_id     TEXT NOT NULL,
    canonical_id TEXT NOT NULL,
    observed_at  TEXT NOT NULL,
    PRIMARY KEY (source, track_id, canonical_id)
)
""",
    # Ordered per-provider snapshots of each playlist, kept as a short history.
    # A recovery / forensics trail (what did this playlist look like, in order,
    # and when) — the sync logic itself never reads these back.
    """
CREATE TABLE IF NOT EXISTS playlist_order (
    playlist    TEXT NOT NULL,
    source      TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    tracks      TEXT NOT NULL,
    PRIMARY KEY (playlist, source, captured_at)
)
""",
]

UPSERT = """
INSERT INTO songs (source, id, isrc, name, artist, album, duration_ms, meta, first_seen, last_seen)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(source, id) DO UPDATE SET
    isrc = excluded.isrc, name = excluded.name, artist = excluded.artist,
    album = excluded.album, duration_ms = excluded.duration_ms,
    meta = excluded.meta, last_seen = excluded.last_seen
"""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path):
    # check_same_thread=False: the Apple and YT mirrors run on separate threads,
    # each with its own use of a connection; the timeout rides out any lock.
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    # Migrate a pre-per-provider playlist_state (no `source` column). It's
    # regenerable snapshot state, so drop it and let the schema recreate it.
    cols = [r[1] for r in conn.execute("PRAGMA table_info(playlist_state)").fetchall()]
    if cols and "source" not in cols:
        conn.execute("DROP TABLE playlist_state")
    for schema in SCHEMAS:
        conn.execute(schema)
    # Existing non-empty baselines predate playlist_state_meta. Mark them as
    # initialized in place; providers with no rows remain correctly classified
    # as bootstrap peers on their next N-way pass.
    now = _now()
    conn.execute(
        "INSERT OR IGNORE INTO playlist_state_meta (playlist, source, initialized_at) "
        "SELECT DISTINCT playlist, source, ? FROM playlist_state",
        (now,),
    )
    conn.execute(
        "INSERT OR IGNORE INTO track_identity_history "
        "SELECT source, track_id, canonical_id, updated FROM track_identity",
    )
    conn.commit()
    return conn


def upsert_many(conn, source, tracks):
    """Archive the sync's own snapshot dicts (any service shape). first_seen is
    preserved on refresh; meta keeps the full snapshot as JSON."""
    now = _now()
    rows = []
    for track in tracks:
        song_id = track.get("id") or track.get("catalog_id") or track.get("relationship_id")
        if not song_id:
            continue
        artist = track.get("artist") or ", ".join(track.get("artists") or [])
        rows.append((
            source, song_id, track.get("isrc"), track.get("name"), artist,
            track.get("album"), track.get("duration_ms"),
            json.dumps(track, ensure_ascii=False), now, now,
        ))
    if rows:
        conn.executemany(UPSERT, rows)
        conn.commit()
    return len(rows)


def get_links(conn, target, spotify_ids):
    """{spotify_id: target_id} for previously matched tracks."""
    out = {}
    ids = [i for i in spotify_ids if i]
    for i in range(0, len(ids), 500):
        chunk = ids[i : i + 500]
        marks = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"SELECT spotify_id, target_id FROM links WHERE target = ? AND spotify_id IN ({marks})",
            [target, *chunk],
        )
        out.update(dict(rows.fetchall()))
    return out


def set_links(conn, target, mapping):
    # ponytail: links are trusted forever; delete a row to force re-resolution
    # if a linked id ever goes stale (e.g. a regional catalog pull).
    rows = [(sid, target, tid, _now()) for sid, tid in mapping.items() if sid and tid]
    if rows:
        conn.executemany("INSERT OR REPLACE INTO links VALUES (?, ?, ?, ?)", rows)
        conn.commit()


def get_state(conn, pair, target):
    return conn.execute(
        "SELECT snapshot_id, target_count FROM sync_state WHERE pair = ? AND target = ?", (pair, target)
    ).fetchone()


def set_state(conn, pair, target, snapshot_id, target_count):
    conn.execute(
        "INSERT OR REPLACE INTO sync_state VALUES (?, ?, ?, ?, ?)",
        (pair, target, snapshot_id, target_count, _now()),
    )
    conn.commit()


def _in_chunks(conn, sql, prefix, ids):
    out = {}
    ids = [i for i in ids if i]
    for i in range(0, len(ids), 500):
        chunk = ids[i : i + 500]
        marks = ",".join("?" * len(chunk))
        rows = conn.execute(sql.format(marks=marks), [*prefix, *chunk])
        out.update(dict(rows.fetchall()))
    return out


def get_reverse_links(conn, target, target_ids):
    """{target_id: spotify_id} — the inverse of get_links, so a non-Spotify
    track can be traced back to its canonical Spotify identity."""
    return _in_chunks(
        conn, "SELECT target_id, spotify_id FROM links WHERE target = ? AND target_id IN ({marks})",
        [target], target_ids)


def get_isrcs(conn, source, ids):
    """{id: isrc} from the songs archive for a source (only rows that have one)."""
    got = _in_chunks(
        conn, "SELECT id, isrc FROM songs WHERE source = ? AND isrc IS NOT NULL AND id IN ({marks})",
        [source], ids)
    return {k: v for k, v in got.items() if v}


def get_identities(conn, source, track_ids):
    """{track_id: canonical_id} recorded for this provider's existing tracks."""
    return _in_chunks(
        conn, "SELECT track_id, canonical_id FROM track_identity WHERE source = ? "
              "AND track_id IN ({marks})", [source], track_ids)


def get_identity_history(conn, source, track_ids):
    """{track_id: {canonical_ids}} retained across hard-identity corrections."""
    out = {}
    ids = [track_id for track_id in track_ids if track_id]
    for i in range(0, len(ids), 500):
        chunk = ids[i : i + 500]
        marks = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"SELECT track_id, canonical_id FROM track_identity_history "
            f"WHERE source = ? AND track_id IN ({marks})",
            [source, *chunk],
        )
        for track_id, canonical_id in rows:
            out.setdefault(track_id, set()).add(canonical_id)
    return out


def set_identities(conn, source, mapping):
    """Remember what each track resolved to. Only hard ids are ever stored, so a
    later degraded read yields to the identity the entry already earned."""
    rows = [(source, tid, cid, _now()) for tid, cid in mapping.items() if tid and cid]
    if rows:
        try:
            conn.executemany(
                "INSERT OR IGNORE INTO track_identity_history VALUES (?, ?, ?, ?)", rows)
            conn.executemany("INSERT OR REPLACE INTO track_identity VALUES (?, ?, ?, ?)", rows)
            conn.commit()
        except Exception:
            conn.rollback()
            raise


ORDER_HISTORY_KEEP = 12


def record_order(conn, playlist, source, entries):
    """Append one ordered snapshot ([[track_id, name, artist], ...]) of a
    provider's playlist — skipped when identical to the newest stored one, and
    pruned to the last ORDER_HISTORY_KEEP per (playlist, source)."""
    payload = json.dumps(entries, ensure_ascii=False)
    last = conn.execute(
        "SELECT tracks FROM playlist_order WHERE playlist = ? AND source = ? "
        "ORDER BY captured_at DESC LIMIT 1", (playlist, source)).fetchone()
    if last and last[0] == payload:
        return
    conn.execute("INSERT OR REPLACE INTO playlist_order VALUES (?, ?, ?, ?)",
                 (playlist, source, _now(), payload))
    conn.execute(
        "DELETE FROM playlist_order WHERE playlist = ? AND source = ? AND captured_at NOT IN ("
        "SELECT captured_at FROM playlist_order WHERE playlist = ? AND source = ? "
        "ORDER BY captured_at DESC LIMIT ?)",
        (playlist, source, playlist, source, ORDER_HISTORY_KEEP))
    conn.commit()


def get_order_history(conn, playlist, source):
    """[(captured_at, [[track_id, name, artist], ...]), ...] newest first."""
    rows = conn.execute(
        "SELECT captured_at, tracks FROM playlist_order WHERE playlist = ? AND source = ? "
        "ORDER BY captured_at DESC", (playlist, source))
    return [(ts, json.loads(t)) for ts, t in rows.fetchall()]


def get_playlist_state(conn, playlist, source):
    rows = conn.execute("SELECT canonical_id FROM playlist_state WHERE playlist = ? AND source = ?",
                        (playlist, source))
    return {r[0] for r in rows.fetchall()}


def has_playlist_state(conn, playlist, source):
    """Whether this provider has an initialized N-way baseline, including an
    intentionally empty one. Membership rows are accepted for compatibility
    with databases created before playlist_state_meta existed."""
    row = conn.execute(
        "SELECT 1 FROM playlist_state_meta WHERE playlist = ? AND source = ? "
        "UNION ALL "
        "SELECT 1 FROM playlist_state WHERE playlist = ? AND source = ? LIMIT 1",
        (playlist, source, playlist, source),
    ).fetchone()
    return row is not None


def get_pending_removals(conn, playlist, source):
    """Canonical ids absent on one prior trusted N-way pass for this source."""
    rows = conn.execute(
        "SELECT canonical_id FROM playlist_pending_removal "
        "WHERE playlist = ? AND source = ?",
        (playlist, source),
    )
    return {row[0] for row in rows.fetchall()}


def commit_reconcile_membership(conn, playlist, state_updates, pending_updates):
    """Atomically replace selected baselines and pending removal observations.

    A source absent from either mapping is untouched. An empty set explicitly
    clears that side. This runs only after provider writes succeed, keeping a
    crash or exception retry-safe: state never claims an external write landed
    when it did not, and a failed pass never confirms a deletion.
    """
    now = _now()
    try:
        for source, canonical_ids in state_updates.items():
            conn.execute(
                "INSERT OR REPLACE INTO playlist_state_meta VALUES (?, ?, ?)",
                (playlist, source, now),
            )
            conn.execute(
                "DELETE FROM playlist_state WHERE playlist = ? AND source = ?",
                (playlist, source),
            )
            conn.executemany(
                "INSERT OR IGNORE INTO playlist_state VALUES (?, ?, ?)",
                [(playlist, source, cid) for cid in canonical_ids],
            )
        for source, canonical_ids in pending_updates.items():
            canonical_ids = set(canonical_ids)
            existing = get_pending_removals(conn, playlist, source)
            conn.executemany(
                "DELETE FROM playlist_pending_removal "
                "WHERE playlist = ? AND source = ? AND canonical_id = ?",
                [(playlist, source, cid) for cid in existing - canonical_ids],
            )
            conn.executemany(
                "INSERT OR IGNORE INTO playlist_pending_removal VALUES (?, ?, ?, ?)",
                [(playlist, source, cid, now) for cid in canonical_ids],
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def set_playlist_state(conn, playlist, source, canonical_ids):
    """Replace one provider's baseline and clear obsolete pending removals."""
    commit_reconcile_membership(
        conn, playlist, {source: canonical_ids}, {source: set()})


def set_reconcile_identities(conn, playlist, repaired_states, learned_identities):
    """Atomically persist identity learning and any source-local baseline repair.

    A stable physical entry can move from one hard canonical id to another as
    provider metadata improves. Committing the new ``track_identity`` without
    remapping its old playlist baseline loses the evidence of that transition
    and makes the next pass look like a deletion. Keep both sides in one SQLite
    transaction, after every provider read has succeeded.
    """
    now = _now()
    try:
        for source, canonical_ids in repaired_states.items():
            conn.execute(
                "INSERT OR REPLACE INTO playlist_state_meta VALUES (?, ?, ?)",
                (playlist, source, now),
            )
            conn.execute(
                "DELETE FROM playlist_state WHERE playlist = ? AND source = ?",
                (playlist, source),
            )
            conn.executemany(
                "INSERT OR IGNORE INTO playlist_state VALUES (?, ?, ?)",
                [(playlist, source, cid) for cid in canonical_ids],
            )
            conn.execute(
                "DELETE FROM playlist_pending_removal WHERE playlist = ? AND source = ?",
                (playlist, source),
            )
        rows = [
            (source, track_id, canonical_id, now)
            for source, mapping in learned_identities.items()
            for track_id, canonical_id in mapping.items()
            if track_id and canonical_id
        ]
        if rows:
            conn.executemany(
                "INSERT OR IGNORE INTO track_identity_history VALUES (?, ?, ?, ?)", rows)
            conn.executemany("INSERT OR REPLACE INTO track_identity VALUES (?, ?, ?, ?)", rows)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def clear_playlist_state(conn, playlist):
    """Drop a playlist's stored N-way baselines (every source) — the next pass
    re-bootstraps from what's actually on each provider, so out-of-band edits
    (e.g. the duplicate cleanup) are never read back as user deletions."""
    conn.execute("DELETE FROM playlist_state WHERE playlist = ?", (playlist,))
    conn.execute("DELETE FROM playlist_state_meta WHERE playlist = ?", (playlist,))
    conn.execute("DELETE FROM playlist_pending_removal WHERE playlist = ?", (playlist,))
    conn.commit()
