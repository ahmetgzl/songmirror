"""Small, provider-neutral helpers for persisted OAuth token files.

Spotify and YouTube Music bring their own token-cache implementations.  The
plain-HTTP providers use this module so their connectors and engine targets
agree on one durable, owner-only file without pulling the web/settings layer
into the engine.
"""

import json
import os
import time
from pathlib import Path


def token_path(env_name: str, default: str) -> str:
    """Resolve a provider token path, preferring an explicit environment value."""
    return os.getenv(env_name) or default


def read_token(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def write_token(path: str, token: dict) -> None:
    """Persist a token with owner-only permissions where the OS supports them."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            fd = -1
            json.dump(token, f, indent=2)
    finally:
        if fd >= 0:
            os.close(fd)


def with_expiry(token: dict) -> dict:
    """Copy an OAuth response and turn relative ``expires_in`` into epoch time."""
    out = dict(token)
    if out.get("expires_in") is not None:
        try:
            out["expires_at"] = int(time.time()) + int(out["expires_in"])
        except (TypeError, ValueError):
            pass
    return out


def token_is_live(token: dict, leeway: int = 90) -> bool:
    access = token.get("access_token")
    if not access:
        return False
    expires_at = token.get("expires_at")
    if expires_at in (None, "", 0, "0"):
        return True
    try:
        return float(expires_at) > time.time() + leeway
    except (TypeError, ValueError):
        return False


def merge_refresh(old: dict, fresh: dict) -> dict:
    """Merge a refresh response without losing an omitted refresh/user token."""
    merged = dict(old)
    merged.update({k: v for k, v in with_expiry(fresh).items() if v is not None})
    return merged
