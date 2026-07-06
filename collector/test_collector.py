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


import asyncio
import logging
import logging.handlers
import os

import watchfiles


def test_setup_logging_configures_rotating_file_handler(tmp_path):
    # Deliberately NOT a dotted child of "ai_dash.collector" (e.g. not
    # "ai_dash.collector.test_rotating") — a child logger propagates its
    # records up to the parent's handlers by default, which would make any
    # `.info()`/`.error()` call on this test logger also write into the real
    # production logger's real ~/.ai_dash/collector.log.
    test_logger = collector_mod._setup_logging(
        log_dir=tmp_path, name="test_ai_dash_rotating_config"
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
    monkeypatch.setattr(collector_mod, "SOURCES", {"anthropic": tmp_path})

    # Redirect the module's logger to a fully isolated one (same reasoning as
    # above) so watch()'s internal logger.info/error calls never touch the
    # real ~/.ai_dash/collector.log during this test.
    test_logger = collector_mod._setup_logging(
        log_dir=tmp_path, name="test_ai_dash_watch_fallback"
    )
    monkeypatch.setattr(collector_mod, "logger", test_logger)

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

    caplog.set_level("ERROR", logger="test_ai_dash_watch_fallback")
    asyncio.run(collector_mod.watch("https://example.test", "test-key"))

    assert fallback_calls == [("https://example.test", "test-key")]
    assert any(
        "falling back to stdlib polling" in record.message
        for record in caplog.records
    )


def test_provider_for_path_resolves_correct_source(tmp_path, monkeypatch):
    anthropic_dir = tmp_path / "claude"
    openai_dir = tmp_path / "codex"
    monkeypatch.setattr(collector_mod, "SOURCES", {"anthropic": anthropic_dir, "openai": openai_dir})

    assert collector_mod._provider_for_path(anthropic_dir / "sub" / "file.jsonl") == "anthropic"
    assert collector_mod._provider_for_path(openai_dir / "file.jsonl") == "openai"


def test_provider_for_path_defaults_to_anthropic_for_unrecognized_path(tmp_path, monkeypatch):
    monkeypatch.setattr(collector_mod, "SOURCES", {"anthropic": tmp_path / "claude"})
    assert collector_mod._provider_for_path(tmp_path / "elsewhere" / "file.jsonl") == "anthropic"


def test_sync_all_stdlib_skips_missing_sources(tmp_path, monkeypatch):
    existing = tmp_path / "exists"
    existing.mkdir()
    missing = tmp_path / "missing"
    monkeypatch.setattr(collector_mod, "SOURCES", {"anthropic": existing, "openai": missing})
    monkeypatch.setattr(collector_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(collector_mod, "STATE_FILE", tmp_path / "state.json")

    result = collector_mod._sync_all_stdlib("https://example.test", "test-key", {})
    assert result == {}


def test_ship_urllib_sends_x_provider_header(tmp_path, monkeypatch):
    f = tmp_path / "session.jsonl"
    f.write_text("hello world")

    captured = {}

    class FakeResponse:
        def read(self):
            return json.dumps({"id": "abc", "status": "done"}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, context=None, timeout=None):
        captured["provider"] = req.get_header("X-provider")
        return FakeResponse()

    monkeypatch.setattr(collector_mod.urllib.request, "urlopen", fake_urlopen)

    ok, new_offset, _ = collector_mod._ship_urllib(
        f, "https://example.test", "test-key", "openai", offset=0, mtime=1.0
    )

    assert ok
    assert captured["provider"] == "openai"


def test_sync_all_dispatches_correct_provider_per_source(tmp_path, monkeypatch):
    anthropic_dir = tmp_path / "claude"
    anthropic_dir.mkdir()
    (anthropic_dir / "session1.jsonl").write_text("data1")
    openai_dir = tmp_path / "codex"
    openai_dir.mkdir()
    (openai_dir / "session2.jsonl").write_text("data2")

    monkeypatch.setattr(collector_mod, "SOURCES", {"anthropic": anthropic_dir, "openai": openai_dir})

    calls = []

    async def fake_ship(path, url, key, provider, client, offset=0, mtime=0.0):
        calls.append((path.name, provider))
        return True, len(path.read_text()), 10

    monkeypatch.setattr(collector_mod, "ship", fake_ship)

    class FakeClient:
        pass

    asyncio.run(collector_mod.sync_all("https://example.test", "test-key", {}, FakeClient()))

    assert ("session1.jsonl", "anthropic") in calls
    assert ("session2.jsonl", "openai") in calls


def test_watch_rechecks_sources_after_periodic_timeout(tmp_path, monkeypatch):
    # A source directory that doesn't exist yet when watch() starts (e.g.
    # ~/.codex/sessions before the user has ever run Codex CLI) must still
    # get picked up once it appears, without requiring a process restart.
    monkeypatch.setattr(collector_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(collector_mod, "STATE_FILE", tmp_path / "state.json")

    anthropic_dir = tmp_path / "claude"
    anthropic_dir.mkdir()
    openai_dir = tmp_path / "codex"  # does not exist on the first watch cycle

    monkeypatch.setattr(collector_mod, "SOURCES", {"anthropic": anthropic_dir, "openai": openai_dir})

    watched_calls = []
    call_count = 0

    async def fake_awatch(*paths):
        nonlocal call_count
        call_count += 1
        watched_calls.append(set(paths))
        if call_count == 1:
            # Simulate the "openai" source appearing while the first watch
            # cycle was in progress, then the periodic recheck timeout firing.
            openai_dir.mkdir()
            raise TimeoutError()
        raise RuntimeError("stop-test-here")
        yield set()  # pragma: no cover — makes this an async generator function

    monkeypatch.setattr(watchfiles, "awatch", fake_awatch)

    fallback_calls = []
    monkeypatch.setattr(
        collector_mod,
        "_watch_poll",
        lambda url, key: fallback_calls.append((url, key)),
    )

    asyncio.run(collector_mod.watch("https://example.test", "test-key"))

    assert len(watched_calls) == 2
    assert watched_calls[0] == {str(anthropic_dir)}
    assert watched_calls[1] == {str(anthropic_dir), str(openai_dir)}
    # The RuntimeError on the second cycle should still hit the existing
    # fallback-to-polling path, confirming that path still works after this change.
    assert fallback_calls == [("https://example.test", "test-key")]
