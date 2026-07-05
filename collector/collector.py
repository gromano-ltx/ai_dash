#!/usr/bin/env python3
"""
ai-dash collector — watches ~/.claude/projects/ and ships transcripts to the central server.
Config: ~/.ai_dash/config.json  {"url": "...", "key": "..."}
State:  ~/.ai_dash/state.json   {"<path>": {"mtime": <float>, "offset": <int>}}

Only new bytes since the last successful ship are sent, gzip-compressed.
The server accumulates content per session and re-parses on each append.

Deps (httpx, watchfiles) are auto-installed on first run.
If pip is unavailable, falls back to stdlib urllib + 10-second polling.
"""

import asyncio
import gzip
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CONFIG_DIR = Path.home() / ".ai_dash"
CONFIG_FILE = CONFIG_DIR / "config.json"
STATE_FILE = CONFIG_DIR / "state.json"
TRANSCRIPTS_BASE = Path.home() / ".claude" / "projects"


def _ensure_deps() -> tuple[bool, bool]:
    """Auto-install httpx and watchfiles if missing. Returns (has_httpx, has_watchfiles)."""
    missing = []
    try:
        import httpx  # noqa: F401
    except ImportError:
        missing.append("httpx")
    try:
        import watchfiles  # noqa: F401
    except ImportError:
        missing.append("watchfiles")

    if not missing:
        return True, True

    print(f"[ai-dash] installing missing packages: {', '.join(missing)}", file=sys.stderr)
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet"] + missing,
            check=True, capture_output=True,
        )
        print(f"[ai-dash] installed {', '.join(missing)}", file=sys.stderr)
        return True, True
    except Exception as exc:
        print(f"[ai-dash] auto-install failed ({exc}), using stdlib fallback", file=sys.stderr)
        has_httpx = "httpx" not in missing
        has_watchfiles = "watchfiles" not in missing
        return has_httpx, has_watchfiles


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        print(f"[ai-dash] No config at {CONFIG_FILE}. Create it with {{\"url\":\"...\",\"key\":\"...\"}}.", file=sys.stderr)
        sys.exit(1)
    try:
        return json.loads(CONFIG_FILE.read_text())
    except Exception as exc:
        print(f"[ai-dash] Config invalid: {exc}", file=sys.stderr)
        sys.exit(1)


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            raw = json.loads(STATE_FILE.read_text())
            return {
                k: v if isinstance(v, dict) else {"mtime": v, "offset": 0}
                for k, v in raw.items()
            }
        except Exception:
            pass
    return {}


def save_state(state: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = STATE_FILE.with_suffix(STATE_FILE.suffix + ".tmp")
    tmp_path.write_text(json.dumps(state))
    os.replace(tmp_path, STATE_FILE)


# ── stdlib path (polling + urllib) ───────────────────────────────────────────

def _ship_urllib(
    path: Path, url: str, key: str, offset: int = 0, mtime: float = 0.0
) -> tuple[bool, int, int]:
    """Returns (ok, new_offset, compressed_bytes_sent)."""
    try:
        raw_bytes = path.read_bytes()
    except Exception as exc:
        print(f"[ai-dash] cannot read {path.name}: {exc}", file=sys.stderr)
        return False, offset, 0

    new_bytes = raw_bytes[offset:]
    if not new_bytes:
        return True, offset, 0

    compressed = gzip.compress(new_bytes)
    req = urllib.request.Request(
        f"{url.rstrip('/')}/api/v1/ingest",
        data=compressed,
        method="POST",
    )
    req.add_header("X-API-Key", key)
    req.add_header("Content-Type", "text/plain")
    req.add_header("Content-Encoding", "gzip")
    req.add_header("X-Session-Id", path.stem)
    req.add_header("X-File-Offset", str(offset))
    req.add_header("X-File-Mtime", str(mtime))

    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
            data = json.loads(resp.read())
        new_offset = offset + len(new_bytes)
        print(
            f"[ai-dash] {path.name} → {data.get('id', '?')} ({data.get('status', '?')})  "
            f"{len(new_bytes):,}B raw → {len(compressed):,}B gz"
        )
        return True, new_offset, len(compressed)
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        if exc.code == 400 and "Resend from offset 0" in text:
            print(f"[ai-dash] server lost session for {path.name}, re-sending from offset 0")
            return _ship_urllib(path, url, key, offset=0, mtime=mtime)
        print(f"[ai-dash] server error {exc.code} for {path.name}", file=sys.stderr)
        return False, offset, 0
    except Exception as exc:
        print(f"[ai-dash] failed to ship {path.name}: {exc}", file=sys.stderr)
        return False, offset, 0


def _sync_all_stdlib(url: str, key: str, state: dict) -> dict:
    if not TRANSCRIPTS_BASE.exists():
        return state

    total_raw = total_gz = 0
    for path in TRANSCRIPTS_BASE.rglob("*.jsonl"):
        try:
            stat = path.stat()
            mtime, size = stat.st_mtime, stat.st_size
        except Exception:
            continue

        key_str = str(path)
        entry = state.get(key_str, {"mtime": 0, "offset": 0})
        if entry["mtime"] == mtime and entry["offset"] == size:
            continue

        offset = entry["offset"] if size >= entry["offset"] else 0

        # Three attempts with backoff for transient network errors
        for attempt in range(3):
            ok, new_offset, gz_len = _ship_urllib(path, url, key, offset, mtime)
            if ok:
                total_raw += new_offset - offset
                total_gz += gz_len
                state[key_str] = {"mtime": mtime, "offset": new_offset}
                break
            if attempt < 2:
                time.sleep(2 ** attempt)

    if total_raw:
        ratio = (1 - total_gz / total_raw) * 100
        print(f"[ai-dash] sync complete — {total_raw:,}B raw → {total_gz:,}B gz ({ratio:.0f}% reduction)")
    return state


def _watch_poll(url: str, key: str, interval: int = 10):
    state = load_state()
    print(f"[ai-dash] starting (polling every {interval}s) — syncing to {url}")
    state = _sync_all_stdlib(url, key, state)
    save_state(state)

    if not TRANSCRIPTS_BASE.exists():
        print(f"[ai-dash] {TRANSCRIPTS_BASE} not found, will watch once it appears", file=sys.stderr)

    print(f"[ai-dash] watching {TRANSCRIPTS_BASE}")
    while True:
        try:
            time.sleep(interval)
            new_state = _sync_all_stdlib(url, key, state)
            if new_state != state:
                state = new_state
                save_state(state)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            print(f"[ai-dash] poll error: {exc}", file=sys.stderr)


# ── async path (httpx + watchfiles) ─────────────────────────────────────────

async def ship(
    path: Path, url: str, key: str, client, offset: int = 0, mtime: float = 0.0
) -> tuple[bool, int, int]:
    """Returns (ok, new_offset, compressed_bytes_sent)."""
    try:
        raw_bytes = path.read_bytes()
    except Exception as exc:
        print(f"[ai-dash] cannot read {path.name}: {exc}", file=sys.stderr)
        return False, offset, 0

    new_bytes = raw_bytes[offset:]
    if not new_bytes:
        return True, offset, 0

    compressed = gzip.compress(new_bytes)

    # Three attempts with backoff for transient network errors — mirrors
    # _ship_urllib's retry loop below. Without this, the async httpx path
    # (the default whenever httpx/watchfiles are installed) had no retry at
    # all, so a single transient failure left this byte range unsent with
    # no immediate retry.
    for attempt in range(3):
        try:
            resp = await client.post(
                f"{url.rstrip('/')}/api/v1/ingest",
                content=compressed,
                headers={
                    "X-API-Key": key,
                    "Content-Type": "text/plain",
                    "Content-Encoding": "gzip",
                    "X-Session-Id": path.stem,
                    "X-File-Offset": str(offset),
                    "X-File-Mtime": str(mtime),
                },
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                new_offset = offset + len(new_bytes)
                print(
                    f"[ai-dash] {path.name} → {data.get('id', '?')} ({data.get('status', '?')})  "
                    f"{len(new_bytes):,}B raw → {len(compressed):,}B gz"
                )
                return True, new_offset, len(compressed)
            elif resp.status_code == 400 and "Resend from offset 0" in resp.text:
                print(f"[ai-dash] server lost session for {path.name}, re-sending from offset 0")
                return await ship(path, url, key, client, offset=0, mtime=mtime)
            else:
                print(f"[ai-dash] server error {resp.status_code} for {path.name}", file=sys.stderr)
        except Exception as exc:
            print(f"[ai-dash] failed to ship {path.name}: {exc}", file=sys.stderr)

        if attempt < 2:
            await asyncio.sleep(2 ** attempt)

    return False, offset, 0


async def sync_all(url: str, key: str, state: dict, client) -> dict:
    if not TRANSCRIPTS_BASE.exists():
        return state

    total_raw = total_gz = 0
    for path in TRANSCRIPTS_BASE.rglob("*.jsonl"):
        try:
            stat = path.stat()
            mtime, size = stat.st_mtime, stat.st_size
        except Exception:
            continue

        key_str = str(path)
        entry = state.get(key_str, {"mtime": 0, "offset": 0})
        if entry["mtime"] == mtime and entry["offset"] == size:
            continue

        offset = entry["offset"] if size >= entry["offset"] else 0
        ok, new_offset, gz_len = await ship(path, url, key, client, offset, mtime)
        if ok:
            total_raw += new_offset - offset
            total_gz += gz_len
            state[key_str] = {"mtime": mtime, "offset": new_offset}

    if total_raw:
        ratio = (1 - total_gz / total_raw) * 100
        print(f"[ai-dash] sync complete — {total_raw:,}B raw → {total_gz:,}B gz ({ratio:.0f}% reduction)")
    return state


async def watch(url: str, key: str):
    import httpx
    from watchfiles import awatch

    state = load_state()
    async with httpx.AsyncClient() as client:
        print(f"[ai-dash] starting — syncing existing transcripts to {url}")
        state = await sync_all(url, key, state, client)
        save_state(state)

        if not TRANSCRIPTS_BASE.exists():
            print(f"[ai-dash] {TRANSCRIPTS_BASE} not found, will watch once it appears", file=sys.stderr)
            return

        print(f"[ai-dash] watching {TRANSCRIPTS_BASE}")
        async for changes in awatch(str(TRANSCRIPTS_BASE)):
            changed = {Path(p) for _, p in changes if p.endswith(".jsonl")}
            for path in changed:
                try:
                    stat = path.stat()
                    mtime, size = stat.st_mtime, stat.st_size
                except Exception:
                    continue
                key_str = str(path)
                entry = state.get(key_str, {"mtime": 0, "offset": 0})
                offset = entry["offset"] if size >= entry["offset"] else 0
                ok, new_offset, _ = await ship(path, url, key, client, offset, mtime)
                if ok:
                    state[key_str] = {"mtime": mtime, "offset": new_offset}
                    save_state(state)


def main():
    cfg = load_config()
    url = cfg.get("url", "").rstrip("/")
    key = cfg.get("key", "")
    if not url or not key:
        print("[ai-dash] Config missing 'url' or 'key'.", file=sys.stderr)
        sys.exit(1)

    has_httpx, has_watchfiles = _ensure_deps()

    try:
        if has_httpx and has_watchfiles:
            asyncio.run(watch(url, key))
        else:
            _watch_poll(url, key)
    except KeyboardInterrupt:
        print("\n[ai-dash] stopped.")


if __name__ == "__main__":
    main()
