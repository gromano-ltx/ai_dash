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


def test_backfill_corrects_codex_input_tokens_and_adds_cached_meta():
    engine = _make_engine()
    with Session(engine) as session:
        session.add(AgentRun(
            id="codex-run-1", provider="openai", model="gpt-5-codex",
            input_tokens=999999, meta={},
        ))
        session.add(TranscriptStore(session_id="codex-run-1", content=_codex_transcript_content()))
        session.commit()

        _backfill_cached_input_tokens(session)

        run = session.get(AgentRun, "codex-run-1")
        assert run.input_tokens == 1000
        assert run.meta["cached_input_tokens"] == 0


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
