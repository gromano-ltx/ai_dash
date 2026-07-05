# Collector Reliability & Installer (AI-6) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the collector safe to run unattended long-term (atomic state writes, bounded logs, graceful fallback when `watchfiles` breaks at runtime) and give new users a one-command installer that isolates the collector's dependencies in a dedicated virtualenv.

**Architecture:** Two isolated changes to `collector/collector.py` (atomic `state.json` writes; rotating-file logging + runtime fallback from the async `watchfiles` path to the existing stdlib polling path), plus a new `install.sh` at the repo root that creates a dedicated venv, downloads `collector.py`, writes config, and registers a launchd (macOS) / systemd (Linux) service.

**Tech Stack:** Python 3.12 stdlib (`os.replace`, `logging.handlers.RotatingFileHandler`), pytest for unit tests, bash for the installer, launchd/systemd for service supervision.

## Global Constraints

- Dedicated venv for the collector's dependencies (`~/.ai_dash/venv`) — never installs into a shared/system Python environment. (Design: "Shared dependency environment" root cause.)
- `state.json` writes must be atomic (temp file + `os.replace()`), never a partial/truncated write visible to a concurrent reader.
- The async `watchfiles` path must fall back to the existing stdlib polling path (`_watch_poll`) if it fails **at runtime**, not just when the initial dependency check fails.
- Collector logging must be bounded: `RotatingFileHandler` at `~/.ai_dash/collector.log`, `maxBytes=5*1024*1024` (5MB), `backupCount=3` — total cap ≈20MB regardless of uptime.
- Service definitions (launchd plist / systemd unit) redirect stdout/stderr to `/dev/null` — the collector manages its own bounded log file directly, not the service supervisor.
- Installer must be idempotent: re-running it must not error, duplicate the service registration, or re-prompt for config that already exists.
- v1 platform target is macOS (launchd) and Linux (systemd user services) only — no other OS/init system support.

---

### Task 1: Atomic `state.json` writes

**Files:**
- Modify: `collector/collector.py:86-88` (`save_state`)
- Create: `collector/test_collector.py`

**Interfaces:**
- Consumes: none (self-contained; `save_state(state: dict) -> None` keeps its existing signature)
- Produces: `save_state(state: dict) -> None` still writes to the module-level `STATE_FILE` path, but now atomically. No other task depends on new symbols from this task.

- [ ] **Step 1: Write the failing tests**

Create `collector/test_collector.py`:

```python
import json
import os
from pathlib import Path

import collector.collector as collector_mod


def test_save_state_uses_atomic_replace(tmp_path, monkeypatch):
    monkeypatch.setattr(collector_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(collector_mod, "STATE_FILE", tmp_path / "state.json")

    replace_calls = []
    original_replace = os.replace

    def spy_replace(src, dst):
        replace_calls.append((str(src), str(dst)))
        return original_replace(src, dst)

    monkeypatch.setattr(collector_mod.os, "replace", spy_replace)

    collector_mod.save_state({"a": {"mtime": 1.0, "offset": 10}})

    assert len(replace_calls) == 1
    src, dst = replace_calls[0]
    assert dst == str(tmp_path / "state.json")
    assert src.endswith(".tmp")


def test_save_state_writes_correct_content(tmp_path, monkeypatch):
    monkeypatch.setattr(collector_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(collector_mod, "STATE_FILE", tmp_path / "state.json")

    collector_mod.save_state({"a": {"mtime": 1.0, "offset": 10}})

    state_file = tmp_path / "state.json"
    assert state_file.exists()
    assert json.loads(state_file.read_text()) == {"a": {"mtime": 1.0, "offset": 10}}


def test_save_state_leaves_no_tmp_file_behind(tmp_path, monkeypatch):
    monkeypatch.setattr(collector_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(collector_mod, "STATE_FILE", tmp_path / "state.json")

    collector_mod.save_state({"a": {"mtime": 1.0, "offset": 10}})

    leftover = list(tmp_path.glob("*.tmp"))
    assert leftover == []


def test_save_state_overwrites_existing_content(tmp_path, monkeypatch):
    monkeypatch.setattr(collector_mod, "CONFIG_DIR", tmp_path)
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(collector_mod, "STATE_FILE", state_file)
    state_file.write_text(json.dumps({"old": {"mtime": 0.0, "offset": 0}}))

    collector_mod.save_state({"new": {"mtime": 2.0, "offset": 20}})

    assert json.loads(state_file.read_text()) == {"new": {"mtime": 2.0, "offset": 20}}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest collector/test_collector.py -v` (from repo root)
Expected: `test_save_state_uses_atomic_replace` FAILS with
`assert 0 == 1` (or similar) — the current implementation calls
`STATE_FILE.write_text(...)` directly and never calls `os.replace`, so
`replace_calls` stays empty. The other three tests PASS already (the naive
write also produces correct final content and no `.tmp` file) — they exist to
lock in `save_state`'s observable behavior as a regression net, not to prove
the safety property; the real crash-safety guarantee is exercised by the
manual kill-test in Step 5.

- [ ] **Step 3: Implement atomic writes**

Replace `collector/collector.py:86-88`:

```python
def save_state(state: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state))
```

with:

```python
def save_state(state: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = STATE_FILE.with_suffix(STATE_FILE.suffix + ".tmp")
    tmp_path.write_text(json.dumps(state))
    os.replace(tmp_path, STATE_FILE)
```

`os` is already imported at the top of `collector/collector.py` (line 17) — no new import needed.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest collector/test_collector.py -v`
Expected: `4 passed`

- [ ] **Step 5: Manual verification — survives a kill mid-write**

This exercises the actual atomicity guarantee (`os.replace` is atomic at the
filesystem level; a `.tmp` file killed mid-write never becomes the live
`state.json`), which the fast unit tests above can't observe on their own:

```bash
python3 - <<'PY'
import os, signal
from pathlib import Path
import collector.collector as c

test_dir = Path("/tmp/ai_dash_atomic_test")
test_dir.mkdir(exist_ok=True)
c.CONFIG_DIR = test_dir
c.STATE_FILE = test_dir / "state.json"

c.save_state({"seed": {"mtime": 1.0, "offset": 1}})

pid = os.fork()
if pid == 0:
    big_state = {str(i): {"mtime": 1.0, "offset": i} for i in range(500_000)}
    c.save_state(big_state)
    os._exit(0)
else:
    os.kill(pid, signal.SIGKILL)
    os.waitpid(pid, 0)
PY
python3 -c "import json; json.load(open('/tmp/ai_dash_atomic_test/state.json')); print('VALID JSON — atomic write held')"
rm -rf /tmp/ai_dash_atomic_test
```

Expected output: `VALID JSON — atomic write held` (the killed child either never
reached `os.replace` — leaving the seed content intact — or completed it fully;
`state.json` is never left as half-written garbage).

- [ ] **Step 6: Commit**

```bash
git add collector/collector.py collector/test_collector.py
git commit -m "fix: make collector state.json writes atomic (AI-6)"
```

---

### Task 2: Rotating-file logging + graceful watchfiles runtime fallback

**Files:**
- Modify: `collector/collector.py` (module-level logging setup; replace all `print(...)` calls; wrap the `awatch` loop in `watch()`)
- Modify: `collector/test_collector.py` (append tests)

**Interfaces:**
- Consumes: `save_state` from Task 1 (unchanged signature, already atomic — no interaction needed).
- Produces: module-level `logger` (a `logging.Logger` named `"ai_dash.collector"`) and `_setup_logging(log_dir: Path = CONFIG_DIR, name: str = "ai_dash.collector") -> logging.Logger`. Task 3 (installer) relies on the log file living at `CONFIG_DIR / "collector.log"` (i.e. `~/.ai_dash/collector.log`) for its README/service documentation — no code dependency, just the fixed path.

- [ ] **Step 1: Write the failing tests**

Append to `collector/test_collector.py`:

```python
import asyncio
import logging
import logging.handlers
import os

import watchfiles


def test_setup_logging_configures_rotating_file_handler(tmp_path):
    test_logger = collector_mod._setup_logging(
        log_dir=tmp_path, name="ai_dash.collector.test_rotating"
    )

    file_handlers = [
        h for h in test_logger.handlers
        if isinstance(h, logging.handlers.RotatingFileHandler)
    ]
    assert len(file_handlers) == 1
    handler = file_handlers[0]
    assert handler.maxBytes == 5 * 1024 * 1024
    assert handler.backupCount == 3
    assert handler.baseFilename == os.path.abspath(str(tmp_path / "collector.log"))


def test_watch_falls_back_to_polling_on_awatch_runtime_failure(
    tmp_path, monkeypatch, caplog
):
    monkeypatch.setattr(collector_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(collector_mod, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(collector_mod, "TRANSCRIPTS_BASE", tmp_path)

    async def fake_awatch(path):
        raise RuntimeError("_rust_notify broken")
        yield  # pragma: no cover — makes this an async generator function

    monkeypatch.setattr(watchfiles, "awatch", fake_awatch)

    fallback_calls = []
    monkeypatch.setattr(
        collector_mod,
        "_watch_poll",
        lambda url, key: fallback_calls.append((url, key)),
    )

    caplog.set_level("ERROR")
    asyncio.run(collector_mod.watch("https://example.test", "test-key"))

    assert fallback_calls == [("https://example.test", "test-key")]
    assert any(
        "falling back to stdlib polling" in record.message
        for record in caplog.records
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest collector/test_collector.py -v`
Expected: `test_setup_logging_configures_rotating_file_handler` FAILS with
`AttributeError: module 'collector.collector' has no attribute '_setup_logging'`.
`test_watch_falls_back_to_polling_on_awatch_runtime_failure` FAILS because the
current `watch()` lets the `RuntimeError` propagate uncaught out of `asyncio.run(...)`
instead of calling `_watch_poll`.

- [ ] **Step 3: Implement logging setup and replace all `print` calls**

Add near the top of `collector/collector.py`, after the existing imports (after line 24 `from pathlib import Path`):

```python
import logging
import logging.handlers
```

Add after the `TRANSCRIPTS_BASE` constant (after line 29):

```python
LOG_FILE = CONFIG_DIR / "collector.log"


def _setup_logging(log_dir: Path = CONFIG_DIR, name: str = "ai_dash.collector") -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_logger = logging.getLogger(name)
    log_logger.setLevel(logging.INFO)
    if not log_logger.handlers:
        formatter = logging.Formatter("%(asctime)s %(message)s")
        file_handler = logging.handlers.RotatingFileHandler(
            log_dir / "collector.log", maxBytes=5 * 1024 * 1024, backupCount=3
        )
        file_handler.setFormatter(formatter)
        log_logger.addHandler(file_handler)
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        log_logger.addHandler(stream_handler)
    return log_logger


logger = _setup_logging()
```

Now replace every `print(...)` call in the file with the equivalent `logger` call.
Messages keep their existing text (minus the leading `"[ai-dash] "` — the log
formatter's timestamp prefix makes it redundant); anything currently printed with
`file=sys.stderr` becomes `logger.error` (or `logger.warning` for the two
"not found, will watch once it appears" messages, which aren't fatal), everything
else becomes `logger.info`. Apply these exact replacements:

| Location (function) | Old | New |
|---|---|---|
| `_ensure_deps` | `print(f"[ai-dash] installing missing packages: {', '.join(missing)}", file=sys.stderr)` | `logger.info(f"installing missing packages: {', '.join(missing)}")` |
| `_ensure_deps` | `print(f"[ai-dash] installed {', '.join(missing)}", file=sys.stderr)` | `logger.info(f"installed {', '.join(missing)}")` |
| `_ensure_deps` | `print(f"[ai-dash] auto-install failed ({exc}), using stdlib fallback", file=sys.stderr)` | `logger.error(f"auto-install failed ({exc}), using stdlib fallback")` |
| `load_config` | `print(f"[ai-dash] No config at {CONFIG_FILE}. Create it with {{\"url\":\"...\",\"key\":\"...\"}}.", file=sys.stderr)` | `logger.error(f"No config at {CONFIG_FILE}. Create it with {{\"url\":\"...\",\"key\":\"...\"}}.")` |
| `load_config` | `print(f"[ai-dash] Config invalid: {exc}", file=sys.stderr)` | `logger.error(f"Config invalid: {exc}")` |
| `_ship_urllib` | `print(f"[ai-dash] cannot read {path.name}: {exc}", file=sys.stderr)` | `logger.error(f"cannot read {path.name}: {exc}")` |
| `_ship_urllib` | `print(f"[ai-dash] {path.name} → {data.get('id', '?')} ({data.get('status', '?')})  " f"{len(new_bytes):,}B raw → {len(compressed):,}B gz")` | `logger.info(f"{path.name} → {data.get('id', '?')} ({data.get('status', '?')})  " f"{len(new_bytes):,}B raw → {len(compressed):,}B gz")` |
| `_ship_urllib` | `print(f"[ai-dash] server lost session for {path.name}, re-sending from offset 0")` | `logger.info(f"server lost session for {path.name}, re-sending from offset 0")` |
| `_ship_urllib` | `print(f"[ai-dash] server error {exc.code} for {path.name}", file=sys.stderr)` | `logger.error(f"server error {exc.code} for {path.name}")` |
| `_ship_urllib` | `print(f"[ai-dash] failed to ship {path.name}: {exc}", file=sys.stderr)` | `logger.error(f"failed to ship {path.name}: {exc}")` |
| `_sync_all_stdlib` | `print(f"[ai-dash] sync complete — {total_raw:,}B raw → {total_gz:,}B gz ({ratio:.0f}% reduction)")` | `logger.info(f"sync complete — {total_raw:,}B raw → {total_gz:,}B gz ({ratio:.0f}% reduction)")` |
| `_watch_poll` | `print(f"[ai-dash] starting (polling every {interval}s) — syncing to {url}")` | `logger.info(f"starting (polling every {interval}s) — syncing to {url}")` |
| `_watch_poll` | `print(f"[ai-dash] {TRANSCRIPTS_BASE} not found, will watch once it appears", file=sys.stderr)` | `logger.warning(f"{TRANSCRIPTS_BASE} not found, will watch once it appears")` |
| `_watch_poll` | `print(f"[ai-dash] watching {TRANSCRIPTS_BASE}")` | `logger.info(f"watching {TRANSCRIPTS_BASE}")` |
| `_watch_poll` | `print(f"[ai-dash] poll error: {exc}", file=sys.stderr)` | `logger.error(f"poll error: {exc}")` |
| `ship` (async) | `print(f"[ai-dash] cannot read {path.name}: {exc}", file=sys.stderr)` | `logger.error(f"cannot read {path.name}: {exc}")` |
| `ship` (async) | `print(f"[ai-dash] {path.name} → {data.get('id', '?')} ({data.get('status', '?')})  " f"{len(new_bytes):,}B raw → {len(compressed):,}B gz")` | `logger.info(f"{path.name} → {data.get('id', '?')} ({data.get('status', '?')})  " f"{len(new_bytes):,}B raw → {len(compressed):,}B gz")` |
| `ship` (async) | `print(f"[ai-dash] server lost session for {path.name}, re-sending from offset 0")` | `logger.info(f"server lost session for {path.name}, re-sending from offset 0")` |
| `ship` (async) | `print(f"[ai-dash] server error {resp.status_code} for {path.name}", file=sys.stderr)` | `logger.error(f"server error {resp.status_code} for {path.name}")` |
| `ship` (async) | `print(f"[ai-dash] failed to ship {path.name}: {exc}", file=sys.stderr)` | `logger.error(f"failed to ship {path.name}: {exc}")` |
| `sync_all` | `print(f"[ai-dash] sync complete — {total_raw:,}B raw → {total_gz:,}B gz ({ratio:.0f}% reduction)")` | `logger.info(f"sync complete — {total_raw:,}B raw → {total_gz:,}B gz ({ratio:.0f}% reduction)")` |
| `watch` | `print(f"[ai-dash] starting — syncing existing transcripts to {url}")` | `logger.info(f"starting — syncing existing transcripts to {url}")` |
| `watch` | `print(f"[ai-dash] {TRANSCRIPTS_BASE} not found, will watch once it appears", file=sys.stderr)` | `logger.warning(f"{TRANSCRIPTS_BASE} not found, will watch once it appears")` |
| `watch` | `print(f"[ai-dash] watching {TRANSCRIPTS_BASE}")` | `logger.info(f"watching {TRANSCRIPTS_BASE}")` |
| `main` | `print("[ai-dash] Config missing 'url' or 'key'.", file=sys.stderr)` | `logger.error("Config missing 'url' or 'key'.")` |
| `main` | `print("\n[ai-dash] stopped.")` | `logger.info("stopped.")` |

- [ ] **Step 4: Implement the graceful runtime fallback in `watch()`**

Replace the body of `watch()` (currently `collector/collector.py:291-320`) with:

```python
async def watch(url: str, key: str):
    import httpx
    from watchfiles import awatch

    state = load_state()
    async with httpx.AsyncClient() as client:
        logger.info(f"starting — syncing existing transcripts to {url}")
        state = await sync_all(url, key, state, client)
        save_state(state)

        if not TRANSCRIPTS_BASE.exists():
            logger.warning(f"{TRANSCRIPTS_BASE} not found, will watch once it appears")
            return

        logger.info(f"watching {TRANSCRIPTS_BASE}")
        try:
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
        except Exception as exc:
            logger.error(f"watchfiles failed at runtime ({exc}), falling back to stdlib polling")
            _watch_poll(url, key)
```

This is the same logic as before, wrapped in a `try`/`except` around the
`async for` loop only (not the initial sync, which must still raise normally
if it fails before the fallback point is even reached).

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest collector/test_collector.py -v`
Expected: `6 passed`

- [ ] **Step 6: Manual verification — bounded log growth**

```bash
python3 - <<'PY'
from pathlib import Path
import collector.collector as c

test_dir = Path("/tmp/ai_dash_log_test")
test_dir.mkdir(exist_ok=True)
test_logger = c._setup_logging(log_dir=test_dir, name="ai_dash.collector.manual_rotation_test")
line = "x" * 1000
for _ in range(20_000):
    test_logger.info(line)
PY
du -sh /tmp/ai_dash_log_test
ls /tmp/ai_dash_log_test
rm -rf /tmp/ai_dash_log_test
```

Expected: `ls` shows `collector.log`, `collector.log.1`, `collector.log.2`,
`collector.log.3` (4 files, capped — no `collector.log.4`), and `du -sh` reports
a total around 20MB, not the ~20MB-per-file/unbounded growth the old plain-file
approach would have produced for 20,000 × 1000-byte lines (~20MB of raw input,
confirming rotation kicked in rather than growing past the 4-file cap).

- [ ] **Step 7: Commit**

```bash
git add collector/collector.py collector/test_collector.py
git commit -m "feat: bounded rotating logs + graceful watchfiles fallback in collector (AI-6)"
```

---

### Task 3: One-liner installer script + README update

**Files:**
- Create: `install.sh` (repo root — already served at `GET /install.sh` by `backend/main.py:111-113`, which reads `_ROOT / "install.sh"` where `_ROOT` is the repo root)
- Modify: `README.md:83` (Collector setup section)

**Interfaces:**
- Consumes: the log file path (`~/.ai_dash/collector.log`) and rotation policy (5MB × 3 backups) established in Task 2, for the README's description. `GET /collector.py` (already implemented, `backend/main.py:106-108`) as the download source. No new backend interfaces are needed — both routes already exist.
- Produces: nothing consumed by other tasks — this is the final task in the plan.

- [ ] **Step 1: Write `install.sh`**

Create `install.sh` at the repo root:

```bash
#!/usr/bin/env bash
set -euo pipefail

AI_DASH_URL="${AI_DASH_URL:-https://dash.ai-coordinator.io}"
AI_DASH_DIR="$HOME/.ai_dash"
VENV_DIR="$AI_DASH_DIR/venv"
COLLECTOR_PY="$AI_DASH_DIR/collector.py"
CONFIG_FILE="$AI_DASH_DIR/config.json"

echo "[ai-dash] installing to $AI_DASH_DIR"
mkdir -p "$AI_DASH_DIR"

# 1. Dedicated venv — isolated from any other project's Python environment,
#    so an unrelated `pip install` elsewhere can never break the collector's deps.
if [ ! -d "$VENV_DIR" ]; then
  echo "[ai-dash] creating dedicated virtualenv at $VENV_DIR"
  python3 -m venv "$VENV_DIR"
else
  echo "[ai-dash] virtualenv already exists at $VENV_DIR, reusing"
fi
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet httpx watchfiles

# 2. Download collector.py (always refreshed, even on re-run)
echo "[ai-dash] downloading collector.py from $AI_DASH_URL"
curl -fsSL "$AI_DASH_URL/collector.py" -o "$COLLECTOR_PY"

# 3. Config — prompt only if missing, so re-running is idempotent
if [ ! -f "$CONFIG_FILE" ]; then
  read -rp "ai-dash API key: " AI_DASH_KEY
  cat > "$CONFIG_FILE" <<EOF
{"url": "$AI_DASH_URL", "key": "$AI_DASH_KEY"}
EOF
  echo "[ai-dash] wrote config to $CONFIG_FILE"
else
  echo "[ai-dash] config already exists at $CONFIG_FILE, leaving as-is"
fi

# 4. Service definition + load/start (always rewritten + reloaded, safe to re-run)
OS_NAME="$(uname -s)"
if [ "$OS_NAME" = "Darwin" ]; then
  PLIST="$HOME/Library/LaunchAgents/com.ai-dash.collector.plist"
  mkdir -p "$HOME/Library/LaunchAgents"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.ai-dash.collector</string>
    <key>ProgramArguments</key>
    <array>
        <string>$VENV_DIR/bin/python</string>
        <string>$COLLECTOR_PY</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/dev/null</string>
    <key>StandardErrorPath</key>
    <string>/dev/null</string>
</dict>
</plist>
EOF
  launchctl unload "$PLIST" >/dev/null 2>&1 || true
  launchctl load "$PLIST"
  echo "[ai-dash] launchd service installed and started"
elif [ "$OS_NAME" = "Linux" ]; then
  SERVICE_DIR="$HOME/.config/systemd/user"
  SERVICE_FILE="$SERVICE_DIR/ai-dash-collector.service"
  mkdir -p "$SERVICE_DIR"
  cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=ai-dash collector

[Service]
ExecStart=$VENV_DIR/bin/python $COLLECTOR_PY
Restart=always
StandardOutput=null
StandardError=null

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now ai-dash-collector.service
  echo "[ai-dash] systemd service installed and started"
else
  echo "[ai-dash] unsupported OS '$OS_NAME' — run manually: $VENV_DIR/bin/python $COLLECTOR_PY" >&2
  exit 1
fi

echo "[ai-dash] done. Dashboard: $AI_DASH_URL"
echo "[ai-dash] logs: $AI_DASH_DIR/collector.log"
```

Make it executable:

```bash
chmod +x install.sh
```

- [ ] **Step 2: Update `README.md`**

Replace `README.md:83`:

```markdown
**Run as a background service** — see `install.sh` for launchd (macOS) / systemd (Linux) setup.
```

with:

```markdown
**Run as a background service (recommended)** — the installer creates a dedicated virtualenv
(isolated from any other Python project on your machine), downloads the collector, and registers
it as a launchd (macOS) / systemd (Linux) service that restarts automatically and logs to
`~/.ai_dash/collector.log` (rotated at 5MB × 3 backups, ~20MB max):

```bash
curl -fsSL https://dash.ai-coordinator.io/install.sh | bash
```

Re-running the command is safe — it reuses the existing virtualenv and config, and just refreshes
the collector code and service definition.
```

- [ ] **Step 3: Manual verification — idempotent install on this machine**

```bash
cd /Users/gromano/repos/ai_dash
python3 -m http.server 8123 --directory . &
HTTP_PID=$!
sleep 1
AI_DASH_URL="http://localhost:8123" bash -c '
  curl -fsSL "$AI_DASH_URL/collector.py" -o /tmp/ai_dash_install_test_collector.py
  test -s /tmp/ai_dash_install_test_collector.py && echo "download OK"
'
kill $HTTP_PID
rm -f /tmp/ai_dash_install_test_collector.py
```

Expected: `download OK` — confirms the `GET /collector.py`-style download step
fetches real content (this stands in for the full curl-from-production flow,
which the plan doesn't run against the live server to avoid registering a real
collector instance for a test run).

Since this is a bash installer with no unit test framework in this repo, verify
the venv/service logic by actually running it once on this machine (macOS,
per the current environment):

```bash
AI_DASH_URL="https://dash.ai-coordinator.io" bash install.sh
# enter the real API key when prompted
AI_DASH_URL="https://dash.ai-coordinator.io" bash install.sh
# second run: should print "virtualenv already exists" and "config already exists", no prompt
launchctl list | grep ai-dash
# expect: com.ai-dash.collector listed with a PID
```

Note: the systemd (Linux) branch cannot be manually verified on this machine
(macOS) — it's covered by code review of the script only, not a live run. Flag
this to the user rather than claiming Linux was tested.

- [ ] **Step 4: Commit**

```bash
git add install.sh README.md
git commit -m "feat: add one-liner collector installer with isolated venv (AI-6)"
```

---

## Self-Review

**Spec coverage:** Architecture & flow (venv, download, config prompt, service def, load/start, confirmation) → Task 3. Atomic `state.json` writes → Task 1. Graceful runtime fallback → Task 2. Bounded rotating logging → Task 2. Idempotent re-run → Task 3. All four design items covered.

**Placeholder scan:** No TBD/TODO; every step shows complete code or an exact command with expected output.

**Type consistency:** `_setup_logging(log_dir: Path = CONFIG_DIR, name: str = "ai_dash.collector") -> logging.Logger` is defined once in Task 2 and used identically in its own tests; no other task references it. `save_state(state: dict) -> None` signature is unchanged from the original across all tasks.
