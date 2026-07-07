from sqlmodel import Session, select

from backend.models import User


def test_user_round_trips_through_db(test_client):
    import backend.db as db_module

    with Session(db_module.engine) as session:
        session.add(User(username="alice", password_hash="hashed", is_admin=True))
        session.commit()

        loaded = session.exec(select(User).where(User.username == "alice")).one()
        assert loaded.username == "alice"
        assert loaded.password_hash == "hashed"
        assert loaded.is_admin is True
        assert loaded.created_at is not None


def test_agent_run_cost_fields_default_to_none_and_round_trip(test_client):
    from sqlmodel import Session, select
    import backend.db as db_module
    from backend.models import AgentRun

    with Session(db_module.engine) as session:
        run = AgentRun(id="cost-test-run", provider="anthropic", model="m")
        session.add(run)
        session.commit()

        loaded = session.exec(select(AgentRun).where(AgentRun.id == "cost-test-run")).one()
        assert loaded.estimated_input_cost_usd is None
        assert loaded.estimated_output_cost_usd is None
        assert loaded.estimated_cost_usd is None

        loaded.estimated_input_cost_usd = 1.5
        loaded.estimated_output_cost_usd = 3.0
        loaded.estimated_cost_usd = 4.5
        session.add(loaded)
        session.commit()

        reloaded = session.exec(select(AgentRun).where(AgentRun.id == "cost-test-run")).one()
        assert reloaded.estimated_input_cost_usd == 1.5
        assert reloaded.estimated_output_cost_usd == 3.0
        assert reloaded.estimated_cost_usd == 4.5
