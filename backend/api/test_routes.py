import json

import pytest
from fastapi import HTTPException
from sqlmodel import Session

import backend.db as db_module
from backend.adapters import claude_code, codex, gemini_cli
from backend.api.routes import _parents_with_running_children, _select_parser, _to_read
from backend.models import AgentRun, ApiKey


def test_select_parser_dispatches_anthropic():
    assert _select_parser("anthropic") is claude_code.parse_transcript_content


def test_select_parser_dispatches_openai():
    assert _select_parser("openai") is codex.parse_transcript_content


def test_select_parser_dispatches_gemini():
    assert _select_parser("gemini") is gemini_cli.parse_transcript_content


def test_select_parser_rejects_unknown_provider():
    # Previously silently fell back to the Claude Code parser, mislabeling
    # any typo'd/case-mismatched/not-yet-supported provider as
    # provider="anthropic" with no error. Must now reject explicitly.
    for bad_provider in ("bogus", "Anthropic", "openAI", "geminiCLI"):
        with pytest.raises(HTTPException) as exc_info:
            _select_parser(bad_provider)
        assert exc_info.value.status_code == 422


def _line(d: dict) -> str:
    return json.dumps(d)


def _sample_transcript() -> str:
    """A minimal, schema-accurate synthetic Claude Code session transcript
    with enough combined tokens to clear MIN_TOKENS_TO_PERSIST."""
    lines = [
        _line({
            "type": "user",
            "timestamp": "2026-04-16T16:01:55.000Z",
            "isMeta": True,
            "sessionId": "sess-first-ingest",
            "gitBranch": "main",
            "cwd": "/Users/gromano/repos/ai_dash",
        }),
        _line({
            "type": "assistant",
            "timestamp": "2026-04-16T16:02:00.000Z",
            "sessionId": "sess-first-ingest",
            "requestId": "req-1",
            "message": {
                "model": "claude-sonnet-4-6",
                "usage": {"input_tokens": 10, "output_tokens": 200},
                "content": [],
            },
        }),
    ]
    return "\n".join(lines) + "\n"


def test_ingest_transcript_first_insert_returns_200_not_500(test_client):
    """Regression test for AI-53: ingesting a brand-new session (no
    pre-existing AgentRun row) used to raise DetachedInstanceError and
    return a false 500, because routes.py accessed `run.user` for the SSE
    broadcast payload after `_upsert()` had already expired/detached `run`
    by committing and closing its own Session. Retries "worked" only
    because the second attempt takes the `existing` branch in `_upsert`
    instead of touching the original `run` object."""
    with Session(db_module.engine) as session:
        api_key = ApiKey(user="gabby")
        session.add(api_key)
        session.commit()
        key_value = api_key.key

    response = test_client.post(
        "/api/v1/ingest",
        content=_sample_transcript(),
        headers={
            "x-api-key": key_value,
            "x-provider": "anthropic",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] != "skipped"


def _seed(session, **overrides) -> AgentRun:
    defaults = dict(provider="anthropic", model="m", input_tokens=10, output_tokens=10)
    defaults.update(overrides)
    run = AgentRun(**defaults)
    session.add(run)
    return run


def test_list_runs_filters_by_provider(test_client):
    with Session(db_module.engine) as session:
        _seed(session, id="run-anthropic", provider="anthropic")
        _seed(session, id="run-openai", provider="openai")
        session.commit()

    res = test_client.get("/api/runs?provider=openai")
    assert res.status_code == 200
    assert {r["id"] for r in res.json()} == {"run-openai"}


def test_list_runs_filters_by_status(test_client):
    with Session(db_module.engine) as session:
        _seed(session, id="run-running", status="running")
        _seed(session, id="run-done", status="done")
        session.commit()

    res = test_client.get("/api/runs?status=running")
    assert res.status_code == 200
    assert {r["id"] for r in res.json()} == {"run-running"}


def test_list_runs_filters_by_user_query_param(test_client):
    with Session(db_module.engine) as session:
        _seed(session, id="run-alice", user="alice")
        _seed(session, id="run-bob", user="bob")
        session.commit()

    res = test_client.get("/api/runs?user=alice")
    assert res.status_code == 200
    assert {r["id"] for r in res.json()} == {"run-alice"}


def test_list_runs_ticket_filter_matches_case_insensitively(test_client):
    with Session(db_module.engine) as session:
        _seed(session, id="run-ticketed", ticket_refs=["AI-46"])
        _seed(session, id="run-other", ticket_refs=["AI-99"])
        session.commit()

    res = test_client.get("/api/runs?ticket=ai-46")
    assert res.status_code == 200
    assert {r["id"] for r in res.json()} == {"run-ticketed"}


def test_list_runs_ticket_filter_tolerates_surrounding_whitespace(test_client):
    with Session(db_module.engine) as session:
        _seed(session, id="run-ticketed", ticket_refs=["AI-46"])
        session.commit()

    res = test_client.get("/api/runs", params={"ticket": "  AI-46  "})
    assert res.status_code == 200
    assert {r["id"] for r in res.json()} == {"run-ticketed"}


def test_list_runs_ticket_filter_matches_substring(test_client):
    with Session(db_module.engine) as session:
        _seed(session, id="run-46", ticket_refs=["AI-46"])
        _seed(session, id="run-99", ticket_refs=["AI-99"])
        session.commit()

    res = test_client.get("/api/runs?ticket=46")
    assert res.status_code == 200
    assert {r["id"] for r in res.json()} == {"run-46"}


def test_parents_with_running_children_returns_parent_ids(test_client):
    with Session(db_module.engine) as session:
        _seed(session, id="parent1", status="done")
        _seed(session, id="child1", parent_id="parent1", status="running")
        session.commit()

        result = _parents_with_running_children(session, ["parent1"])
        assert result == {"parent1"}


def test_parents_with_running_children_excludes_parent_with_done_child(test_client):
    with Session(db_module.engine) as session:
        _seed(session, id="parent1", status="done")
        _seed(session, id="child1", parent_id="parent1", status="done")
        session.commit()

        result = _parents_with_running_children(session, ["parent1"])
        assert result == set()


def test_parents_with_running_children_empty_run_ids_returns_empty_set(test_client):
    with Session(db_module.engine) as session:
        assert _parents_with_running_children(session, []) == set()


def test_to_read_overrides_done_status_when_id_in_running_parents():
    run = AgentRun(id="parent1", provider="anthropic", model="m", status="done")
    read = _to_read(run, running_parents={"parent1"})
    assert read.status == "running"


def test_to_read_leaves_done_status_when_id_not_in_running_parents():
    run = AgentRun(id="parent1", provider="anthropic", model="m", status="done")
    read = _to_read(run, running_parents=set())
    assert read.status == "done"


def test_to_read_leaves_running_status_untouched_when_id_in_running_parents():
    run = AgentRun(id="parent1", provider="anthropic", model="m", status="running")
    read = _to_read(run, running_parents={"parent1"})
    assert read.status == "running"


def test_list_runs_shows_done_parent_as_running_when_child_still_running(test_client):
    # A parent's own transcript can go idle (tripping its done-timeout) while
    # it waits on a still-running Task-tool subagent — the parent's reported
    # status must reflect the child's activity, not its own idle transcript.
    with Session(db_module.engine) as session:
        _seed(session, id="parent1", status="done")
        _seed(session, id="child1", parent_id="parent1", status="running")
        session.commit()

    res = test_client.get("/api/runs")
    assert res.status_code == 200
    runs = {r["id"]: r for r in res.json()}
    # Subagent rows are excluded from the default listing.
    assert "child1" not in runs
    assert runs["parent1"]["status"] == "running"


def test_list_runs_leaves_done_parent_as_done_when_child_also_done(test_client):
    with Session(db_module.engine) as session:
        _seed(session, id="parent1", status="done")
        _seed(session, id="child1", parent_id="parent1", status="done")
        session.commit()

    res = test_client.get("/api/runs")
    assert res.status_code == 200
    runs = {r["id"]: r for r in res.json()}
    assert runs["parent1"]["status"] == "done"


def test_get_run_shows_done_parent_as_running_when_child_still_running(test_client):
    with Session(db_module.engine) as session:
        _seed(session, id="parent1", status="done")
        _seed(session, id="child1", parent_id="parent1", status="running")
        session.commit()

    res = test_client.get("/api/runs/parent1")
    assert res.status_code == 200
    assert res.json()["status"] == "running"


def _sample_codex_transcript() -> str:
    """A minimal, schema-accurate synthetic Codex CLI session transcript."""
    lines = [
        _line({
            "timestamp": "2026-04-16T16:01:55.734Z",
            "type": "session_meta",
            "payload": {"id": "codex-sess-1", "cwd": "/tmp", "git": {}},
        }),
        _line({
            "timestamp": "2026-04-16T16:02:00.000Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {"input_tokens": 50, "output_tokens": 200},
                    "last_token_usage": {"input_tokens": 50, "cached_input_tokens": 0},
                },
            },
        }),
    ]
    return "\n".join(lines) + "\n"


def test_ingest_transcript_openai_provider_ignores_parent_id_header(test_client):
    """AI-51 finding 4: codex.py's parser no longer accepts parent_id (it has
    no subagent/nested-session file convention), but the collector's shared
    ingest route still accepts an X-Parent-Id header uniformly across
    providers. Sending one for x-provider=openai must not raise a TypeError
    from routes.py's parse_fn(...) call."""
    with Session(db_module.engine) as session:
        api_key = ApiKey(user="gabby")
        session.add(api_key)
        session.commit()
        key_value = api_key.key

    response = test_client.post(
        "/api/v1/ingest",
        content=_sample_codex_transcript(),
        headers={
            "x-api-key": key_value,
            "x-provider": "openai",
            "x-parent-id": "some-parent-id",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] != "skipped"
