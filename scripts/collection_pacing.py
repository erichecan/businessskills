"""Shared XHS collection queue across projects and subprocesses (macOS/Linux).

Configuration: ~/.config/xhs-collection/pacing.json. All callers use the same
lock and timestamp. Delays are deliberate rate limits, not a login workaround.
"""
import asyncio
import contextvars
import fcntl
import functools
import json
import math
import os
import random
import time
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

DEFAULTS = {
    "request_gap_seconds": 15,
    "search_dwell_seconds": 20,
    "note_dwell_seconds": 30,
    "close_delay_seconds": 8,
    "keyword_gap_seconds": 45,
    "scroll_step_seconds": 5,
    "jitter_ratio": 0.5,
}
_active = contextvars.ContextVar("collection_pacing_active", default=False)


def directory():
    return Path(os.environ.get("XHS_PACING_DIR", str(Path.home() / ".config/xhs-collection")))


def settings():
    values = DEFAULTS.copy()
    path = directory() / "pacing.json"
    if path.exists():
        overrides = json.loads(path.read_text())
        for key, value in overrides.items():
            minimum = 0 if key == "jitter_ratio" else 1
            if key not in values or isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < minimum or (key == "jitter_ratio" and value > 3):
                raise ValueError(f"Invalid collection pacing: {key}={value!r}")
            values[key] = value
    return values


def random_delay(key, values=None):
    """Sample independently per action, always preserving the minimum delay."""
    values = settings() if values is None else values
    minimum = values[key]
    return random.uniform(minimum, minimum * (1 + values["jitter_ratio"]))


def _open_lock():
    directory().mkdir(parents=True, exist_ok=True)
    return (directory() / "queue.lock").open("a+")


def _remaining(handle, action, values):
    handle.seek(0)
    raw = handle.read()
    state = json.loads(raw) if raw.strip() else {}
    now = time.time()
    due = state.get("finished", 0) + random_delay("request_gap_seconds", values)
    if action == "search":
        due = max(due, state.get("searched", 0) + random_delay("keyword_gap_seconds", values))
    return max(0, due - now), state


def _finish(handle, action, state):
    state["finished"] = time.time()
    if action == "search":
        state["searched"] = state["finished"]
    handle.seek(0)
    handle.truncate()
    json.dump(state, handle)
    handle.flush()


@contextmanager
def session(action="note"):
    """Serialize whole browser operations, including their page dwell/cleanup."""
    values = settings()
    with _open_lock() as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        wait, state = _remaining(handle, action, values)
        try:
            if wait:
                time.sleep(wait)
            yield values
        finally:
            _finish(handle, action, state)
            fcntl.flock(handle, fcntl.LOCK_UN)


@asynccontextmanager
async def async_session(action="note"):
    """Nonblocking lock acquisition; cancellation releases the lock immediately."""
    if _active.get():
        yield settings()
        return
    values = settings()
    with _open_lock() as handle:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                await asyncio.sleep(0.25)
        token = _active.set(True)
        state = {}
        try:
            wait, state = _remaining(handle, action, values)
            if wait:
                await asyncio.sleep(wait)
            yield values
        finally:
            _finish(handle, action, state)
            _active.reset(token)
            fcntl.flock(handle, fcntl.LOCK_UN)


def paced_request(func):
    """Acquire before signing; nested request() calls reuse the same slot."""
    @functools.wraps(func)
    async def wrapped(self, *args, **kwargs):
        if _active.get():
            return await func(self, *args, **kwargs)
        route = str(args[0] if args else kwargs.get("url", ""))
        action = "search" if "/search/" in route else "note"
        if func.__name__ == "query_self":
            action = "check"
        async with async_session(action) as values:
            result = await func(self, *args, **kwargs)
            if action != "check":
                await asyncio.sleep(random_delay("search_dwell_seconds" if action == "search" else "note_dwell_seconds", values))
            return result
    return wrapped


def opencli_env():
    """Load a Page dwell hook only for collection subprocesses."""
    import shutil
    binary = shutil.which("opencli") or "/opt/homebrew/bin/opencli"
    page_module = Path(binary).resolve().parent / "browser/page.js"
    if not page_module.is_file():
        raise FileNotFoundError(f"opencli Page module unavailable: {page_module}")
    hook = Path(__file__).resolve().with_suffix(".mjs")
    values = settings()
    return {
        **os.environ,
        "NODE_OPTIONS": (os.environ.get("NODE_OPTIONS", "") + " --import=" + hook.as_uri()).strip(),
        "XHS_PACING_PAGE_MODULE": page_module.as_uri(),
        "XHS_PACING_SETTINGS": json.dumps(values),
    }
