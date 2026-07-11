import logging

from sqlalchemy import text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import backend.db as db_module
from backend.db import _backfill_cached_input_tokens, _backfill_ticket_refs
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


def test_backfill_finds_codex_transcript_when_session_id_differs_from_run_id():
    # Real Codex transcript filenames are "rollout-<timestamp>-<uuid>.jsonl" —
    # the collector ships path.stem as X-Session-Id, but AgentRun.id is just
    # the bare uuid parsed out of the transcript's "session_meta" payload
    # (see backend/adapters/codex.py). TranscriptStore.session_id is keyed on
    # the collector's raw value, which never equals AgentRun.id for Codex —
    # confirmed against production data: 0 of 9 real Codex rows were ever
    # correctly backfilled by this function before this fix (AI-55).
    engine = _make_engine()
    run_id = "019f379e-23e9-7030-b7de-3b6127745f15"
    raw_filename_stem = f"rollout-2026-07-06T16-28-58-{run_id}"
    with Session(engine) as session:
        session.add(AgentRun(
            id=run_id, provider="openai", model="gpt-5-codex",
            input_tokens=999999, output_tokens=0, meta={},
        ))
        session.add(TranscriptStore(
            session_id=raw_filename_stem, run_id=run_id,
            content=_codex_transcript_content().replace("codex-run-1", run_id),
        ))
        session.commit()

        _backfill_cached_input_tokens(session)

        run = session.get(AgentRun, run_id)
        assert run.input_tokens == 1000
        assert run.meta["cached_input_tokens"] == 0


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
        assert run.meta["cache_creation_input_tokens"] == 0


def test_backfill_reruns_anthropic_row_missing_cache_creation_key():
    # Simulates a row migrated under the older cached_input_tokens-only fix:
    # present, but cache_creation_input_tokens (added later) is not — the
    # backfill must not treat this row as fully migrated.
    engine = _make_engine()
    with Session(engine) as session:
        session.add(AgentRun(
            id="claude-run-1", provider="anthropic", model="claude-sonnet-4-6",
            input_tokens=5, output_tokens=200, meta={"cached_input_tokens": 300},
        ))
        session.add(TranscriptStore(session_id="claude-run-1", content=_claude_code_transcript_content()))
        session.commit()

        _backfill_cached_input_tokens(session)

        run = session.get(AgentRun, "claude-run-1")
        assert run.meta["cache_creation_input_tokens"] == 0


def test_backfill_does_not_rerun_openai_row_missing_cache_creation_key():
    # Codex has no cache_creation_input_tokens concept — a row already
    # migrated on cached_input_tokens alone must stay skipped (no
    # TranscriptStore row here, so a re-run would otherwise be a silent no-op
    # anyway, but this asserts the skip condition itself doesn't regress).
    engine = _make_engine()
    with Session(engine) as session:
        session.add(AgentRun(
            id="codex-run-1", provider="openai", model="gpt-5-codex",
            input_tokens=1000, meta={"cached_input_tokens": 0},
        ))
        session.commit()

        _backfill_cached_input_tokens(session)  # must not raise despite no TranscriptStore

        run = session.get(AgentRun, "codex-run-1")
        assert run.input_tokens == 1000
        assert "cache_creation_input_tokens" not in run.meta


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


def _claude_code_transcript_with_pr_self_reference() -> str:
    """A minimal Claude Code transcript where the first user message reads
    like a squash-merge commit message ("... #40 ...") for a PR that's also
    opened via `gh pr create` — the exact shape _extract_tickets used to
    misfile as a ticket ref, duplicating what's already in git_prs."""
    import json

    lines = [
        json.dumps({
            "type": "user", "isMeta": True, "sessionId": "claude-run-2",
            "gitBranch": "main", "cwd": "/repo", "timestamp": "2026-04-16T16:00:00.000Z",
        }),
        json.dumps({
            "type": "user", "sessionId": "claude-run-2", "timestamp": "2026-04-16T16:00:05.000Z",
            "message": {"content": "Merge pull request #40 from foo"},
        }),
        json.dumps({
            "type": "assistant", "sessionId": "claude-run-2", "timestamp": "2026-04-16T16:01:00.000Z",
            "requestId": "req-1",
            "message": {
                "model": "claude-sonnet-4-6",
                "usage": {"input_tokens": 5, "output_tokens": 10},
                "content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash",
                             "input": {"command": "gh pr create --title x"}}],
            },
        }),
        json.dumps({
            "type": "user", "sessionId": "claude-run-2", "timestamp": "2026-04-16T16:01:05.000Z",
            "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_1",
                                      "content": "https://github.com/gromano-ltx/ai_dash/pull/40"}]},
        }),
    ]
    return "\n".join(lines) + "\n"


def test_backfill_ticket_refs_removes_pr_self_reference():
    engine = _make_engine()
    with Session(engine) as session:
        session.add(AgentRun(
            id="claude-run-2", provider="anthropic", model="claude-sonnet-4-6",
            ticket_refs=["#40"], git_prs=["https://github.com/gromano-ltx/ai_dash/pull/40"],
        ))
        session.add(TranscriptStore(session_id="claude-run-2", content=_claude_code_transcript_with_pr_self_reference()))
        session.commit()

        _backfill_ticket_refs(session)

        run = session.get(AgentRun, "claude-run-2")
        assert run.ticket_refs == []
        # Only ticket_refs is touched.
        assert run.git_prs == ["https://github.com/gromano-ltx/ai_dash/pull/40"]


def test_backfill_ticket_refs_is_idempotent_and_skips_unchanged_rows():
    engine = _make_engine()
    with Session(engine) as session:
        session.add(AgentRun(
            id="claude-run-2", provider="anthropic", model="claude-sonnet-4-6",
            ticket_refs=[],
        ))
        session.add(TranscriptStore(session_id="claude-run-2", content=_claude_code_transcript_with_pr_self_reference()))
        session.commit()

        _backfill_ticket_refs(session)  # already matches reparsed value — no-op

        run = session.get(AgentRun, "claude-run-2")
        assert run.ticket_refs == []


def test_backfill_ticket_refs_skips_rows_with_missing_transcript():
    engine = _make_engine()
    with Session(engine) as session:
        session.add(AgentRun(
            id="orphan-2", provider="openai", model="m", ticket_refs=["#99"],
        ))
        session.commit()

        _backfill_ticket_refs(session)  # must not raise

        run = session.get(AgentRun, "orphan-2")
        assert run.ticket_refs == ["#99"]


def _insert_raw_run(session: Session, **overrides) -> AgentRun:
    defaults = dict(
        id="raw-run", provider="anthropic", model="claude-sonnet-4-5-20250929",
        input_tokens=1_000_000, output_tokens=1_000_000,
    )
    defaults.update(overrides)
    run = AgentRun(**defaults)
    session.add(run)
    session.commit()
    return run


def test_add_missing_columns_adds_cost_columns_when_missing(test_client):
    from sqlalchemy import inspect, text

    # The test_client fixture's create_all() already creates these columns
    # on a fresh table, so simulate a genuine pre-migration production table
    # by dropping them first, then verify _add_missing_columns() actually
    # adds them back via its ALTER TABLE branch (SQLite 3.35+ supports
    # DROP COLUMN, matching the Python-bundled sqlite3 version in CI/dev).
    with db_module.engine.begin() as conn:
        conn.execute(text("ALTER TABLE agent_runs DROP COLUMN estimated_input_cost_usd"))
        conn.execute(text("ALTER TABLE agent_runs DROP COLUMN estimated_output_cost_usd"))
        conn.execute(text("ALTER TABLE agent_runs DROP COLUMN estimated_cost_usd"))

    db_module._add_missing_columns()

    inspector = inspect(db_module.engine)
    columns = {c["name"] for c in inspector.get_columns("agent_runs")}
    assert "estimated_input_cost_usd" in columns
    assert "estimated_output_cost_usd" in columns
    assert "estimated_cost_usd" in columns


def test_backfill_computes_cost_for_matched_historical_run(test_client):
    with Session(db_module.engine) as session:
        _insert_raw_run(session)

    db_module._seed()

    with Session(db_module.engine) as session:
        run = session.exec(select(AgentRun).where(AgentRun.id == "raw-run")).one()
        assert run.estimated_input_cost_usd == 3.00
        assert run.estimated_output_cost_usd == 15.00
        assert run.estimated_cost_usd == 18.00


def test_backfill_leaves_unmatched_model_as_none(test_client):
    with Session(db_module.engine) as session:
        _insert_raw_run(session, id="raw-run-unmatched", model="some-unknown-model")

    db_module._seed()

    with Session(db_module.engine) as session:
        run = session.exec(select(AgentRun).where(AgentRun.id == "raw-run-unmatched")).one()
        assert run.estimated_cost_usd is None


def test_backfill_is_idempotent(test_client):
    with Session(db_module.engine) as session:
        _insert_raw_run(session, id="raw-run-idempotent")

    db_module._seed()
    with Session(db_module.engine) as session:
        first_pass = session.exec(
            select(AgentRun).where(AgentRun.id == "raw-run-idempotent")
        ).one().estimated_cost_usd

    db_module._seed()
    with Session(db_module.engine) as session:
        second_pass = session.exec(
            select(AgentRun).where(AgentRun.id == "raw-run-idempotent")
        ).one().estimated_cost_usd

    assert first_pass == second_pass == 18.00


def test_seed_cleanup_logs_instead_of_silently_swallowing_exception(test_client, caplog):
    # AI-20 regression guard: the malformed-data cleanup loop in _seed() used
    # to wrap Postgres-only SQL in a bare `except` that silently rolled back
    # on SQLite, so the cleanup never actually ran and nobody found out. It
    # now catches broadly (row data can be arbitrarily malformed) but must
    # always log via logger.exception rather than passing silently. Force a
    # real failure in that exact loop by corrupting git_commits with a JSON
    # scalar via raw SQL (bypassing model validation) so iterating over it
    # raises TypeError, then assert the failure is actually logged.
    with Session(db_module.engine) as session:
        # input/output tokens set above MIN_TOKENS_TO_PERSIST so this row
        # survives the earlier trivial-session cleanup in _seed() and is
        # still around when the malformed-data loop runs.
        session.add(AgentRun(
            id="malformed-run", provider="anthropic", model="claude-sonnet-4-6",
            input_tokens=1000, output_tokens=1000,
        ))
        session.commit()
        session.exec(text("UPDATE agent_runs SET git_commits = '123' WHERE id = 'malformed-run'"))
        session.commit()

    with caplog.at_level(logging.ERROR, logger="backend.db"):
        db_module._seed()

    assert any(
        "failed to clean up malformed agent_runs data" in r.message and r.levelno >= logging.ERROR
        for r in caplog.records
    )
