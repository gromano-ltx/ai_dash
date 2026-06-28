#!/usr/bin/env python3
"""
ai-dash collector — watches ~/.claude/projects/ and ships transcripts to the central server.
Config: ~/.ai_dash/config.json  {"url": "...", "key": "..."}
State:  ~/.ai_dash/state.json   {"<path>": <mtime>}
"""

import asyncio
import json
import os
import sys
import httpx
from pathlib import Path

CONFIG_DIR = Path.home() / ".ai_dash"
CONFIG_FILE = CONFIG_DIR / "config.json"
STATE_FILE = CONFIG_DIR / "state.json"
TRANSCRIPTS_BASE = Path.home() / ".claude" / "projects"


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        print(f"[ai-dash] No config found at {CONFIG_FILE}. Run the installer.", file=sys.stderr)
        sys.exit(1)
    return json.loads(CONFIG_FILE.read_text())


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def save_state(state: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state))


async def ship(path: Path, url: str, key: str, client: httpx.AsyncClient) -> bool:
    try:
        content = path.read_text(errors="replace")
        resp = await client.post(
            f"{url.rstrip('/')}/api/v1/ingest",
            content=content.encode(),
            headers={"X-API-Key": key, "Content-Type": "text/plain"},
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            print(f"[ai-dash] ingested {path.name} → {data.get('id', '?')} ({data.get('status', '?')})")
            return True
        else:
            print(f"[ai-dash] server error {resp.status_code} for {path.name}", file=sys.stderr)
            return False
    except Exception as exc:
        print(f"[ai-dash] failed to ship {path.name}: {exc}", file=sys.stderr)
        return False


async def sync_all(url: str, key: str, state: dict, client: httpx.AsyncClient) -> dict:
    if not TRANSCRIPTS_BASE.exists():
        return state
    for path in TRANSCRIPTS_BASE.rglob("*.jsonl"):
        try:
            mtime = path.stat().st_mtime
        except Exception:
            continue
        key_str = str(path)
        if state.get(key_str) == mtime:
            continue
        if await ship(path, url, key, client):
            state[key_str] = mtime
    return state


async def watch(url: str, key: str):
    from watchfiles import awatch
    state = load_state()

    async with httpx.AsyncClient() as client:
        print(f"[ai-dash] starting — syncing existing transcripts to {url}")
        state = await sync_all(url, key, state, client)
        save_state(state)

        if not TRANSCRIPTS_BASE.exists():
            print(f"[ai-dash] {TRANSCRIPTS_BASE} not found, waiting...", file=sys.stderr)
            return

        print(f"[ai-dash] watching {TRANSCRIPTS_BASE}")
        async for changes in awatch(str(TRANSCRIPTS_BASE)):
            changed = {Path(p) for _, p in changes if p.endswith(".jsonl")}
            for path in changed:
                try:
                    mtime = path.stat().st_mtime
                except Exception:
                    continue
                if await ship(path, url, key, client):
                    state[str(path)] = mtime
                    save_state(state)


def main():
    cfg = load_config()
    url = cfg.get("url", "").rstrip("/")
    key = cfg.get("key", "")
    if not url or not key:
        print("[ai-dash] Config missing 'url' or 'key'.", file=sys.stderr)
        sys.exit(1)
    try:
        asyncio.run(watch(url, key))
    except KeyboardInterrupt:
        print("\n[ai-dash] stopped.")


if __name__ == "__main__":
    main()
