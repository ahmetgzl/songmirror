"""Merge resolution-cache edits without replacing another worker's history."""

import json
import os
import tempfile
import threading
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path


MATCHING_CACHE_VERSION = 3
_LOCK = threading.RLock()
_MISSING = object()


def _read(path):
    try:
        with open(path, encoding="utf-8") as stream:
            data = json.load(stream)
    except FileNotFoundError:
        data = {}
    # A corrupt file must remain available for recovery, not become an empty
    # cache that the next successful playlist silently writes over.
    manual = set(data.get("manual") or [])
    migrate = bool(data) and data.get("matching_version") != MATCHING_CACHE_VERSION
    search = data.get("search", {})
    return {
        "isrc": {} if migrate else data.get("isrc", {}),
        "search": {key: value for key, value in search.items() if key in manual} if migrate else search,
        "manual": manual,
        "dirty": migrate,
    }


def _snapshot(cache):
    return deepcopy({key: cache[key] for key in ("isrc", "search", "manual")})


def load_cache(cache_file):
    """Load matches and remember the baseline so saves can apply only edits.

    Matching-version upgrades discard automatic results and ISRC candidates,
    while preserving the search keys explicitly chosen by the user.
    """
    with _LOCK:
        cache = _read(cache_file)
    cache["_baseline"] = _snapshot(cache)
    return cache


@contextmanager
def _writer_lock(path):
    """Serialize the read/merge/replace across threads and CLI/server processes."""
    with _LOCK, open(str(path) + ".lock", "a+b") as lock:
        if os.name == "nt":
            import msvcrt

            lock.seek(0, os.SEEK_END)
            if not lock.tell():
                lock.write(b"\0")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _search_state(cache, key):
    return cache["search"].get(key, _MISSING), key in cache.get("manual", ())


def save_cache(cache_file, cache, *, manual_keys=()):
    """Merge this snapshot's changes into current disk data and replace atomically.

    Unchanged entries never overwrite newer values or revive deleted mappings.
    Explicit manual edits beat concurrent matches. Worker normalization of an
    existing manual choice still requires an unchanged baseline. Deletions apply only while
    the disk entry still matches the baseline, preserving concurrent corrections.
    A caller without a baseline can contribute entries, but cannot delete history.
    """
    if not cache.get("dirty", False):
        return
    path = Path(cache_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    manual_keys = set(manual_keys)
    baseline = cache.get("_baseline", {"search": {}, "isrc": {}, "manual": set()})
    with _writer_lock(path):
        merged = _read(path)
        for key in baseline["search"].keys() | cache["search"].keys():
            before = _search_state(baseline, key)
            after = _search_state(cache, key)
            current = _search_state(merged, key)
            if after == before and key not in manual_keys:
                continue
            value, manual = after
            if value is _MISSING:
                if current == before:
                    merged["search"].pop(key, None)
                    merged["manual"].discard(key)
            elif (manual and key in manual_keys) or (current == before and (manual or not current[1])):
                merged["search"][key] = value
                if manual:
                    merged["manual"].add(key)
                else:
                    merged["manual"].discard(key)

        for key in baseline["isrc"].keys() | cache["isrc"].keys():
            before = baseline["isrc"].get(key, _MISSING)
            after = cache["isrc"].get(key, _MISSING)
            if after == before or merged["isrc"].get(key, _MISSING) != before:
                continue
            if after is _MISSING:
                merged["isrc"].pop(key, None)
            else:
                merged["isrc"][key] = after

        payload = {
            "matching_version": MATCHING_CACHE_VERSION,
            "isrc": merged["isrc"], "search": merged["search"],
            "manual": sorted(merged["manual"]),
        }
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
                temporary = stream.name
                json.dump(payload, stream, indent=1)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None and os.path.exists(temporary):
                os.unlink(temporary)
        cache.update(merged)
        cache["dirty"] = False
        cache["_baseline"] = _snapshot(cache)
