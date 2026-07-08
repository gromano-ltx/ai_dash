from sqlmodel import Session, select

import backend.db as db_module
from backend.models import AgentRun
from backend.watcher import _upsert


def test_upsert_computes_and_stores_cost_on_insert(test_client):
    run = AgentRun(
        id="upsert-run", provider="anthropic", model="claude-sonnet-4-5-20250929",
        input_tokens=1_000_000, output_tokens=1_000_000,
    )
    assert _upsert(run) is True

    with Session(db_module.engine) as session:
        stored = session.exec(select(AgentRun).where(AgentRun.id == "upsert-run")).one()
        assert stored.estimated_input_cost_usd == 3.00
        assert stored.estimated_output_cost_usd == 15.00
        assert stored.estimated_cost_usd == 18.00


def test_upsert_recomputes_cost_on_update_as_tokens_grow(test_client):
    first = AgentRun(
        id="growing-run", provider="anthropic", model="claude-sonnet-4-5-20250929",
        input_tokens=500_000, output_tokens=500_000,
    )
    _upsert(first)

    grown = AgentRun(
        id="growing-run", provider="anthropic", model="claude-sonnet-4-5-20250929",
        input_tokens=1_000_000, output_tokens=1_000_000,
    )
    _upsert(grown)

    with Session(db_module.engine) as session:
        stored = session.exec(select(AgentRun).where(AgentRun.id == "growing-run")).one()
        assert stored.estimated_cost_usd == 18.00


def test_upsert_leaves_cost_none_for_unmatched_model(test_client):
    run = AgentRun(
        id="unmatched-run", provider="anthropic", model="some-unknown-model",
        input_tokens=1_000_000, output_tokens=1_000_000,
    )
    _upsert(run)

    with Session(db_module.engine) as session:
        stored = session.exec(select(AgentRun).where(AgentRun.id == "unmatched-run")).one()
        assert stored.estimated_cost_usd is None
