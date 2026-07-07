# AI-6: Collector installer + reliability hardening

## Context

The collector (`collector/collector.py`) watches `~/.claude/projects/` and ships transcripts to
the dashboard server. Today it has no process supervisor: it was run as a bare manual background
process throughout this session. That exposed three real failures in one sitting:

1. **No process supervision**: required manual restarts; `README.md` still points at an
   `install.sh` that was removed from the repo months ago (dead reference).
2. **Shared dependency environment**: the collector's `httpx`/`watchfiles` were installed into the
   same Python environment as an unrelated project. An unrelated `pip install -e .` in that project
   corrupted `watchfiles`' native extension, silently breaking the collector.
3. **Unbounded/unsafe file handling**: `state.json` is read/written with no atomicity, and a
   naive service log (`stdout` redirected to a file) would grow forever with no rotation.

This ticket (folding into **AI-6**, "Installer: one-liner setup script for new users") closes all
three: a proper `curl | bash` installer with an isolated venv and a real background service, plus
two small, targeted hardening changes to `collector.py` itself.

## Architecture & flow

```
User runs: curl https://dash.ai-coordinator.io/install.sh | bash
   │
   ├─ 1. Create ~/.ai_dash/venv (python3 -m venv), install httpx + watchfiles into it:
   │      fully isolated from any other project's Python environment
   ├─ 2. Download collector.py from the server (GET /collector.py, already served by
   │      backend/main.py) into ~/.ai_dash/collector.py
   ├─ 3. Prompt for API key + server URL, write ~/.ai_dash/config.json
   │      (skip prompt if config.json already exists: idempotent)
   ├─ 4. Write a service definition:
   │      macOS: ~/Library/LaunchAgents/com.ai-dash.collector.plist (KeepAlive=true)
   │      Linux: ~/.config/systemd/user/ai-dash-collector.service (Restart=always)
   │      Both invoke: ~/.ai_dash/venv/bin/python ~/.ai_dash/collector.py
   │      Both redirect stdout/stderr → /dev/null (the collector manages its own
   │      bounded log file directly; see Logging below)
   ├─ 5. Load/enable + start the service (launchctl load / systemctl --user enable --now)
   └─ 6. Print confirmation + dashboard URL
```

Re-running the script is safe: each step checks whether its target already exists (venv present,
config present, service already loaded) and skips or updates in place rather than erroring or
duplicating.

## Collector code changes

Two small, targeted changes to `collector/collector.py`, independent of the installer:

### 1. Atomic `state.json` writes

`save_state()` currently writes directly to the target path: a crash or concurrent read mid-write
can see a truncated/corrupt file (this is exactly what required a manual backup before any edit
today). Change it to write to a temp file in the same directory, then `os.replace()` it into place.
`os.replace()` is atomic on both POSIX and Windows, so a reader always sees either the fully-old or
fully-new content, never a partial write.

### 2. Graceful runtime fallback

`watch()` does `from watchfiles import awatch` and lets any failure propagate uncaught. Today's
actual failure: the initial `_ensure_deps()` check only does a top-level `import watchfiles`, which
succeeded even though the deeper native extension (`_rust_notify`) was broken; the real failure
only surfaced later, inside `awatch()`, crashing the whole process. Wrap the `awatch` usage so that
if it raises **after** the initial sync succeeds, the exception is logged and the process falls back
to the existing `_watch_poll()` stdlib implementation instead of exiting, extending the resilience
the module's docstring already promises ("if pip is unavailable, falls back to stdlib") to cover a
broken-at-runtime dependency too, not just a missing one.

### 3. Bounded logging

Replace `collector.py`'s `print(...)`/`print(..., file=sys.stderr)` calls with Python's `logging`
module, configured with a `RotatingFileHandler` writing to `~/.ai_dash/collector.log` (5 MB per
file, 3 backups), capping total disk usage at ~20 MB regardless of how long the service runs. A
`StreamHandler` is also attached so output is still visible when run manually in the foreground.
This is why the service definition (above) redirects stdout/stderr to `/dev/null` rather than a
growing file: the collector manages its own bounded log directly, with no dependency on
`newsyslog`/`logrotate` (which would need root and complicate a user-level installer).

## Error handling / testing plan

- **Installer idempotency**: run the script twice in a row; the second run must detect the existing
  venv/config/service and skip or update cleanly, with no error and no duplicate service
  registration.
- **Atomic writes**: trigger a `save_state()` call and `kill -9` the process immediately after;
  confirm `state.json` is either the complete old or complete new content, never truncated.
- **Graceful fallback**: deliberately break `watchfiles` inside the collector's venv (matching
  today's real failure) and confirm the collector logs the failure and continues operating via the
  stdlib polling path instead of exiting.
- **Bounded logging**: force enough log volume to exceed 5 MB and confirm rotation actually occurs,
  capping at the configured 3-backup limit rather than growing indefinitely.

## Out of scope

- Fixing the currently-broken `install.sh` in a way that supports other cloud providers' installers
  (v1 target is macOS launchd + Linux systemd only, matching AI-6's existing DoD).
- Any change to the collector's core sync/ship logic (offset tracking, retry/backoff): already
  addressed by earlier tickets (AI-27, AI-30, AI-31, AI-32) this session.
- Converting the collector to a fully-fledged daemon framework (e.g. `python-daemon`): launchd/
  systemd already provide supervision; no need for an additional in-process daemonization layer.
