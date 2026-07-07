from sqlmodel import Session, select

import backend.db as db_module
from backend.models import AgentRun


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
