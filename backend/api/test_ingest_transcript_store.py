"""Regression coverage for AI-55: TranscriptStore must be keyed/looked-up by
the content-parsed AgentRun.id, not the collector's raw X-Session-Id header
(which is just the transcript filename's stem). The two only coincide for
Claude Code (main sessions are named "<sessionId>.jsonl", subagents
"agent-<agentId>.jsonl") and Gemini subagents ("<subagent-id>.jsonl") — they
never coincide for Codex ("rollout-<timestamp>-<uuid>.jsonl") or Gemini main
sessions ("session-<timestamp>-<shortid>.jsonl").
"""
import json

from sqlmodel import Session, select

import backend.db as db_module
from backend.adapters.test_claude_code import _sample_session as _claude_main_session
from backend.adapters.test_codex import _sample_session as _codex_session
from backend.adapters.test_gemini_cli import _sample_session as _gemini_main_session
from backend.models import AgentRun, ApiKey, TranscriptStore


def _seed_api_key(key: str = "adk_test123", user: str = "gabby") -> None:
    with Session(db_module.engine) as session:
        session.add(ApiKey(key=key, user=user))
        session.commit()


def _seed_existing_run(run_id: str, provider: str) -> None:
    # Pre-seed the AgentRun row the ingest will update, so watcher._upsert
    # takes its "existing row" branch rather than its "brand new row" branch.
    # The latter is unrelated pre-existing bug territory (AI-53's
    # DetachedInstanceError on first-time ingest, already being fixed on a
    # different branch) — irrelevant to this ticket's TranscriptStore-keying
    # fix, so tests here sidestep it rather than tripping over it.
    with Session(db_module.engine) as session:
        session.add(AgentRun(id=run_id, provider=provider, model="placeholder"))
        session.commit()


def _ingest(test_client, *, session_id: str, provider: str, content: str, api_key: str = "adk_test123"):
    return test_client.post(
        "/api/v1/ingest",
        content=content.encode("utf-8"),
        headers={
            "X-Api-Key": api_key,
            "X-Session-Id": session_id,
            "X-Provider": provider,
        },
    )


def _stored_by_run_id(run_id: str) -> TranscriptStore | None:
    with Session(db_module.engine) as session:
        return session.exec(
            select(TranscriptStore).where(TranscriptStore.run_id == run_id)
        ).first()


def test_codex_transcript_stored_and_findable_by_content_parsed_run_id(test_client):
    _seed_api_key()
    # Real Codex filenames are "rollout-<timestamp>-<uuid>.jsonl" — path.stem
    # (what the collector ships as X-Session-Id) is the whole string, not the
    # bare uuid the adapter parses out of the "session_meta" payload.
    raw_filename_stem = "rollout-2026-04-16T16-01-55-019d9707-10b9-7a42-ba47-8daf19e3639a"
    content = _codex_session()  # session_meta payload id == "019d9707-10b9-7a42-ba47-8daf19e3639a"
    _seed_existing_run("019d9707-10b9-7a42-ba47-8daf19e3639a", provider="openai")

    res = _ingest(test_client, session_id=raw_filename_stem, provider="openai", content=content)

    assert res.status_code == 200
    run_id = res.json()["id"]
    assert run_id == "019d9707-10b9-7a42-ba47-8daf19e3639a"
    assert run_id != raw_filename_stem

    stored = _stored_by_run_id(run_id)
    assert stored is not None
    assert stored.content == content


def test_gemini_main_transcript_stored_and_findable_by_content_parsed_run_id(test_client):
    _seed_api_key()
    # Real Gemini main-session filenames are "session-<timestamp>-<shortid>.jsonl"
    # — again not the same string as the sessionId embedded in the header line.
    raw_filename_stem = "session-2026-06-29T13-49-00-a1b2c3d4"
    content = _gemini_main_session()  # header sessionId == "06ba9b64-5701-4a4e-b7eb-4bac2b449d5c"
    _seed_existing_run("06ba9b64-5701-4a4e-b7eb-4bac2b449d5c", provider="gemini")

    res = _ingest(test_client, session_id=raw_filename_stem, provider="gemini", content=content)

    assert res.status_code == 200
    run_id = res.json()["id"]
    assert run_id == "06ba9b64-5701-4a4e-b7eb-4bac2b449d5c"
    assert run_id != raw_filename_stem

    stored = _stored_by_run_id(run_id)
    assert stored is not None
    assert stored.content == content


def test_claude_code_main_session_transcript_findable_by_run_id_no_regression(test_client):
    _seed_api_key()
    # Claude Code main-session files are named exactly "<sessionId>.jsonl", so
    # path.stem *does* equal AgentRun.id here — must keep working.
    content = _claude_main_session()  # sessionId == "sess-1"
    _seed_existing_run("sess-1", provider="anthropic")

    res = _ingest(test_client, session_id="sess-1", provider="anthropic", content=content)

    assert res.status_code == 200
    run_id = res.json()["id"]
    assert run_id == "sess-1"

    stored = _stored_by_run_id(run_id)
    assert stored is not None
    assert stored.content == content


def test_claude_code_subagent_transcript_findable_by_run_id_no_regression(test_client):
    _seed_api_key()
    # Claude Code subagent files are named "agent-<agentId>.jsonl", matching
    # the "agent-{agent_id}" run_id convention used by the adapter.
    lines = [
        json.dumps({
            "type": "assistant",
            "timestamp": "2026-04-16T16:02:00.000Z",
            "sessionId": "sess-1",
            "agentId": "abc123",
            "requestId": "req-1",
            "message": {
                "model": "claude-sonnet-4-6",
                "usage": {"input_tokens": 5, "output_tokens": 200},
                "content": [],
            },
        }),
    ]
    content = "\n".join(lines) + "\n"
    _seed_existing_run("agent-abc123", provider="anthropic")

    res = _ingest(test_client, session_id="agent-abc123", provider="anthropic", content=content)

    assert res.status_code == 200
    run_id = res.json()["id"]
    assert run_id == "agent-abc123"

    stored = _stored_by_run_id(run_id)
    assert stored is not None
    assert stored.content == content
