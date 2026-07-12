import asyncio
from datetime import datetime, timedelta

from sqlmodel import Session

import backend.db as db_module
import backend.main as main_module
from backend.models import AgentRun


def test_sweep_marks_stale_running_run_as_done(test_client):
    # updated_at 11 minutes ago — past the 10-minute cutoff.
    stale_time = datetime.utcnow() - timedelta(minutes=11)
    with Session(db_module.engine) as session:
        session.add(AgentRun(
            id="stale-run", provider="anthropic", model="m", status="running",
            input_tokens=10, output_tokens=10, updated_at=stale_time,
        ))
        session.commit()

    asyncio.run(main_module._sweep_stale_runs())

    with Session(db_module.engine) as session:
        run = session.get(AgentRun, "stale-run")
        assert run.status == "done"
        assert run.ended_at == stale_time


def test_sweep_leaves_recently_updated_running_run_untouched(test_client):
    # updated_at 1 minute ago — well inside the 10-minute cutoff.
    fresh_time = datetime.utcnow() - timedelta(minutes=1)
    with Session(db_module.engine) as session:
        session.add(AgentRun(
            id="fresh-run", provider="anthropic", model="m", status="running",
            input_tokens=10, output_tokens=10, updated_at=fresh_time,
        ))
        session.commit()

    asyncio.run(main_module._sweep_stale_runs())

    with Session(db_module.engine) as session:
        run = session.get(AgentRun, "fresh-run")
        assert run.status == "running"
        assert run.ended_at is None


def test_sweep_marks_null_updated_at_as_stale(test_client):
    # Rows created before the updated_at column existed have it as NULL —
    # treated as stale so they don't stay "running" forever. A fresh
    # create_all() schema (as this test DB uses) declares the column NOT
    # NULL, and the ORM always backfills its Python-side default when a
    # value is None at flush time, so neither an ORM insert nor a raw SQL
    # UPDATE can produce a genuine NULL against that schema. Reproduce it
    # the way it actually happens in production instead: a row inserted
    # before the column existed, then the real ADD COLUMN migration
    # (backend.db._add_missing_columns) — which adds the column with no
    # NOT NULL constraint — backfills existing rows with NULL.
    from sqlalchemy import text
    with db_module.engine.begin() as conn:
        conn.execute(text("ALTER TABLE agent_runs DROP COLUMN updated_at"))
        conn.execute(
            text(
                "INSERT INTO agent_runs "
                "(id, provider, model, status, started_at, input_tokens, output_tokens, "
                "label, git_commits, git_prs, ticket_refs, meta) "
                "VALUES "
                "('null-updated-run', 'anthropic', 'm', 'running', :now, 10, 10, "
                "'', '[]', '[]', '[]', '{}')"
            ),
            {"now": datetime.utcnow()},
        )
    db_module._add_missing_columns()

    asyncio.run(main_module._sweep_stale_runs())

    with Session(db_module.engine) as session:
        run = session.get(AgentRun, "null-updated-run")
        assert run.status == "done"
        # No real updated_at to use as a proxy — ended_at is left unset
        # rather than fabricating a false duration.
        assert run.ended_at is None


def test_sweep_leaves_done_runs_untouched(test_client):
    stale_time = datetime.utcnow() - timedelta(minutes=11)
    with Session(db_module.engine) as session:
        session.add(AgentRun(
            id="already-done-run", provider="anthropic", model="m", status="done",
            input_tokens=10, output_tokens=10, updated_at=stale_time,
        ))
        session.commit()

    asyncio.run(main_module._sweep_stale_runs())

    with Session(db_module.engine) as session:
        run = session.get(AgentRun, "already-done-run")
        assert run.status == "done"
