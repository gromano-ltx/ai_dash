from sqlalchemy.pool import StaticPool
from sqlmodel import create_engine, Session, SQLModel

from backend.db import _backfill_cached_input_tokens
from backend.models import AgentRun, TranscriptStore


def _make_engine():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _codex_transcript_content() -> str:
    """A minimal, schema-accurate Codex transcript with a known cached-token
    breakdown, for asserting the backfill recomputes input_tokens/meta correctly."""
    import json

    lines = [
        json.dumps({
            "timestamp": "2026-04-16T16:01:55.000Z",
            "type": "session_meta",
            "payload": {"id": "codex-run-1", "cwd": "/repo"},
        }),
        json.dumps({
            "timestamp": "2026-04-16T16:02:10.000Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": 1000, "cached_input_tokens": 0,
                        "output_tokens": 50, "reasoning_output_tokens": 10, "total_tokens": 1050,
                    },
                    "last_token_usage": {
                        "input_tokens": 1000, "cached_input_tokens": 0,
                        "output_tokens": 50, "reasoning_output_tokens": 10, "total_tokens": 1050,
                    },
                    "model_context_window": 272000,
                },
                "rate_limits": {"primary": None, "secondary": None},
            },
        }),
    ]
    return "\n".join(lines) + "\n"


def _claude_code_transcript_content() -> str:
    """A minimal, schema-accurate Claude Code transcript with a known
    cache_read_input_tokens breakdown, for asserting the backfill's anthropic
    path specifically (not just the openai/codex path)."""
    import json

    lines = [
        json.dumps({
            "type": "assistant",
            "timestamp": "2026-04-16T16:02:00.000Z",
            "sessionId": "claude-run-1",
            "requestId": "req-1",
            "message": {
                "model": "claude-sonnet-4-6",
                "usage": {
                    "input_tokens": 5,
                    "output_tokens": 200,
                    "cache_read_input_tokens": 300,
                    "cache_creation_input_tokens": 0,
                },
                "content": [],
            },
        }),
    ]
    return "\n".join(lines) + "\n"


def test_backfill_corrects_codex_input_tokens_and_adds_cached_meta():
    engine = _make_engine()
    with Session(engine) as session:
        session.add(AgentRun(
            id="codex-run-1", provider="openai", model="gpt-5-codex",
            status="done", label="original label",
            input_tokens=999999, output_tokens=0, meta={},
        ))
        session.add(TranscriptStore(session_id="codex-run-1", content=_codex_transcript_content()))
        session.commit()

        _backfill_cached_input_tokens(session)

        run = session.get(AgentRun, "codex-run-1")
        assert run.input_tokens == 1000
        assert run.meta["cached_input_tokens"] == 0
        # Only input_tokens/meta are touched — everything else on the
        # existing row is left exactly as it was, per the plan's constraint.
        assert run.output_tokens == 0
        assert run.status == "done"
        assert run.label == "original label"


def test_backfill_corrects_claude_code_meta_without_touching_input_tokens():
    engine = _make_engine()
    with Session(engine) as session:
        session.add(AgentRun(
            id="claude-run-1", provider="anthropic", model="claude-sonnet-4-6",
            input_tokens=5, output_tokens=200, meta={},
        ))
        session.add(TranscriptStore(session_id="claude-run-1", content=_claude_code_transcript_content()))
        session.commit()

        _backfill_cached_input_tokens(session)

        run = session.get(AgentRun, "claude-run-1")
        # input_tokens/output_tokens are unaffected by this fix for Claude
        # Code (already correct before this ticket) — only meta gains the
        # new key.
        assert run.input_tokens == 5
        assert run.output_tokens == 200
        assert run.meta["cached_input_tokens"] == 300


def test_backfill_is_idempotent_and_skips_already_migrated_rows():
    engine = _make_engine()
    with Session(engine) as session:
        # No TranscriptStore row exists for this id — if the backfill didn't
        # skip already-migrated rows before looking up the transcript, this
        # would either error or silently do nothing useful either way; the
        # real assertion is that input_tokens/meta stay exactly as seeded.
        session.add(AgentRun(
            id="run-1", provider="openai", model="m",
            input_tokens=42, meta={"cached_input_tokens": 5},
        ))
        session.commit()

        _backfill_cached_input_tokens(session)

        run = session.get(AgentRun, "run-1")
        assert run.input_tokens == 42
        assert run.meta["cached_input_tokens"] == 5


def test_backfill_skips_rows_with_missing_transcript():
    engine = _make_engine()
    with Session(engine) as session:
        session.add(AgentRun(
            id="orphan-1", provider="openai", model="m",
            input_tokens=100, meta={},
        ))
        session.commit()

        _backfill_cached_input_tokens(session)  # must not raise

        run = session.get(AgentRun, "orphan-1")
        assert run.input_tokens == 100
        assert "cached_input_tokens" not in (run.meta or {})


def test_backfill_ignores_gemini_rows():
    engine = _make_engine()
    with Session(engine) as session:
        session.add(AgentRun(
            id="gemini-run-1", provider="gemini", model="gemini-3.5-flash",
            input_tokens=777, meta={},
        ))
        session.commit()

        _backfill_cached_input_tokens(session)

        run = session.get(AgentRun, "gemini-run-1")
        assert run.input_tokens == 777
        assert "cached_input_tokens" not in (run.meta or {})
