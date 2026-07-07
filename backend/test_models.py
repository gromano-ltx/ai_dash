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
