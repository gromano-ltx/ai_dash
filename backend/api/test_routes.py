import json

import pytest
from fastapi import HTTPException
from sqlmodel import Session

import backend.db as db_module
from backend.adapters import claude_code, codex, gemini_cli
from backend.api.routes import _select_parser
from backend.models import ApiKey


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
