from itsdangerous import URLSafeTimedSerializer

from backend import auth


def test_hash_password_verifies_correct_password():
    hashed = auth.hash_password("correct horse battery staple")
    assert auth.verify_password("correct horse battery staple", hashed) is True


def test_hash_password_rejects_wrong_password():
    hashed = auth.hash_password("correct horse battery staple")
    assert auth.verify_password("wrong password", hashed) is False


def test_session_token_round_trips_username():
    token = auth.create_session_token("gabby")
    assert auth.verify_session_token(token) == "gabby"


def test_session_token_rejects_tampered_signature():
    token = auth.create_session_token("gabby")
    assert auth.verify_session_token(token + "tampered") is None


def test_session_token_rejects_expired_token(monkeypatch):
    token = auth.create_session_token("gabby")
    monkeypatch.setattr(auth, "SESSION_MAX_AGE_SECONDS", -1)
    assert auth.verify_session_token(token) is None


def test_resolve_session_user_returns_none_for_missing_token(test_client):
    from sqlmodel import Session
    import backend.db as db_module

    with Session(db_module.engine) as session:
        assert auth.resolve_session_user(session, None) is None
        assert auth.resolve_session_user(session, "garbage") is None


def test_resolve_session_user_returns_user_for_valid_token(test_client):
    from sqlmodel import Session
    import backend.db as db_module
    from backend.models import User

    with Session(db_module.engine) as session:
        session.add(User(username="alice", password_hash="x"))
        session.commit()

    token = auth.create_session_token("alice")
    with Session(db_module.engine) as session:
        user = auth.resolve_session_user(session, token)
        assert user is not None
        assert user.username == "alice"
