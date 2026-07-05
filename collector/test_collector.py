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
    monkeypatch.setattr(collector_mod, "TRANSCRIPTS_BASE", tmp_path)

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
