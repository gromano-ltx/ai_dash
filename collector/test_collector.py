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
