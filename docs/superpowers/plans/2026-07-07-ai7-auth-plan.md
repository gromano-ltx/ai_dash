# AI-7: Per-user login Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the dashboard's single shared-password Basic Auth with real per-user accounts, session-cookie login, and per-user data scoping, while keeping `DASHBOARD_PASSWORD` working as a zero-setup fallback until the first account is created.

**Architecture:** A new `users` table (username = the same free-text string already used for `AgentRun.user`/`ApiKey.user`). Passwords hashed with `bcrypt`; sessions are signed `itsdangerous` cookies (no server-side session store). The existing Basic Auth middleware gains a second mode: while no `User` rows exist it behaves exactly as today; once the first account exists, Basic Auth is retired and only a valid session cookie gets through. Non-admin users' run/stats/daily queries are scoped to their own `user` string; admins see everything and can manage accounts and API keys from Settings.

**Tech Stack:** FastAPI + SQLModel (existing), `bcrypt` + `itsdangerous` (new, minimal — see deviation note below), React 19 + TanStack Query (existing, no new frontend deps).

**Source spec:** `docs/superpowers/specs/2026-07-06-ai7-auth-design.md`

## Global Constraints

- No Alembic in this repo — schema changes happen via `SQLModel.metadata.create_all()` (new tables only; the `users` table is net-new, so no hand-rolled `ALTER TABLE` is needed).
- **Deviation from the spec's wording:** the spec says "passlib[bcrypt]"; this plan uses the `bcrypt` package directly instead of `passlib`. Reason found during planning: `passlib` 1.7.4 (latest release) breaks on `bcrypt>=4.1` (a well-known upstream incompatibility — passlib probes `bcrypt.__about__.__version__`, which newer `bcrypt` removed). Using `bcrypt` directly avoids that landmine entirely and is one dependency instead of two. Still satisfies the spec's decision #7 (hand-rolled, small deps, bcrypt-based hashing).
- Follow existing backend conventions: raw `dict` request bodies for POST endpoints (not Pydantic models — matches `create_key`), lazy `from backend.X import Y` imports *inside* functions for anything touching the DB engine at module scope (matches `_cleanup_stale_runs` in `main.py`) so tests can monkeypatch `backend.db.engine` after import, and `# noqa: E711`/`# noqa: E712` comments on `== None`/`== True` comparisons (matches existing style).
- Cookie flags: `httponly=True`, `secure=True` (hardcoded — see Task 5 comment), `samesite="lax"`. No `credentials: 'include'` needed anywhere in the frontend fetch layer — the app is always same-origin (production: frontend served from the same Cloud Run instance; local dev: Vite's `/api` proxy in `vite.config.ts` makes it same-origin from the browser's perspective too), so the browser sends cookies by default.
- `SESSION_SECRET` env var defaults to a fixed dev string (`dev-insecure-secret-change-in-prod`) so `import backend.main` and local dev work with zero setup — mirrors how `DASHBOARD_PASSWORD` defaults to `""` (auth disabled). Production gets a real value via Terraform/Secret Manager (Task 9).
- One-way cutover, by design (per spec decision #4): once the first `User` row exists, `DASHBOARD_PASSWORD` Basic Auth is retired for that deployment — including for whoever just created that first account. Their browser's cached Basic Auth credentials stop working on the very next request; they must go to `/login` and sign in with the new account. This is expected, not a bug — surfaced explicitly in the UI (Task 14).
- The frontend has **no test runner** (no vitest/jest in `frontend/package.json`) and no existing frontend tests — this plan doesn't introduce one. Frontend task verification is manual: `npx tsc --noEmit` (typecheck, matches CI) + exercising the flow with the dev server running.
- Backend tests run with `uv run pytest <path> -v`. CI (`.github/workflows/pr-checks.yml`) currently only runs `collector/test_collector.py` and a bare `import backend.main` check — it does not run `backend/**/test_*.py` today. That gap predates this ticket and is out of scope to fix here; new tests are still written and must pass locally.

---

### Task 1: Backend test infrastructure

Nothing today wires up an in-memory test database + `TestClient` — existing backend tests are pure unit tests with no DB. Every later backend task needs this fixture.

**Files:**
- Create: `backend/conftest.py`
- Test: `backend/test_conftest_smoke.py`

**Interfaces:**
- Produces: pytest fixture `test_client` (a `fastapi.testclient.TestClient` wired to `backend.main.app` with `backend.db.engine` monkeypatched to a fresh in-memory SQLite DB per test).

- [ ] **Step 1: Write `backend/conftest.py`**

```python
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import create_engine
from fastapi.testclient import TestClient

import backend.db as db_module


@pytest.fixture
def test_client(monkeypatch):
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    monkeypatch.setattr(db_module, "engine", test_engine)

    from backend.main import app

    with TestClient(app) as client:
        yield client
```

- [ ] **Step 2: Write the smoke test**

```python
def test_fixture_serves_existing_providers_endpoint(test_client, monkeypatch):
    import backend.main as main_module

    # Force auth off regardless of the developer's shell environment —
    # this test only exists to prove the DB-engine monkeypatch and app
    # lifespan wiring work, before any auth code exists.
    monkeypatch.setattr(main_module, "_DASHBOARD_PASSWORD", "")
    res = test_client.get("/api/providers")
    assert res.status_code == 200
    assert res.json() == {"providers": []}
```

- [ ] **Step 3: Run it**

Run: `uv run pytest backend/test_conftest_smoke.py -v`
Expected: `1 passed`.

- [ ] **Step 4: Commit**

```bash
git add backend/conftest.py backend/test_conftest_smoke.py
git commit -m "test: add in-memory DB + TestClient fixture for backend tests"
```

---

### Task 2: Add `bcrypt` and `itsdangerous` dependencies

**Files:**
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `bcrypt` and `itsdangerous` importable in the venv.

- [ ] **Step 1: Add the dependencies**

Run: `uv add bcrypt itsdangerous`

This updates `pyproject.toml`'s `dependencies` list and `uv.lock`, and installs both into `.venv`.

- [ ] **Step 2: Verify they import**

Run: `uv run python -c "import bcrypt, itsdangerous; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: add bcrypt and itsdangerous for per-user auth"
```

---

### Task 3: `User` model

**Files:**
- Modify: `backend/models.py:40-48` (insert after the `ApiKey` class, before `AgentRunRead`)
- Test: `backend/test_models.py`

**Interfaces:**
- Produces: `backend.models.User` — `SQLModel` table with `username: str` (primary key), `password_hash: str`, `is_admin: bool = False`, `created_at: datetime`.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest backend/test_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'User' from 'backend.models'`

- [ ] **Step 3: Add the model**

In `backend/models.py`, insert this class immediately after the `ApiKey` class (after line 47, before `class AgentRunRead`):

```python
class User(SQLModel, table=True):
    __tablename__ = "users"
    username: str = Field(primary_key=True)
    password_hash: str
    is_admin: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest backend/test_models.py -v`
Expected: `1 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/models.py backend/test_models.py
git commit -m "feat(AI-7): add User model"
```

---

### Task 4: `backend/auth.py` — password hashing, session tokens, auth dependencies

**Files:**
- Create: `backend/auth.py`
- Test: `backend/test_auth.py`

**Interfaces:**
- Consumes: `backend.db.get_session` (existing), `backend.models.User` (Task 3).
- Produces:
  - `COOKIE_NAME: str = "ai_dash_session"`
  - `SESSION_MAX_AGE_SECONDS: int` (30 days)
  - `hash_password(password: str) -> str`
  - `verify_password(password: str, password_hash: str) -> bool`
  - `create_session_token(username: str) -> str`
  - `verify_session_token(token: str) -> Optional[str]` (returns username or `None`)
  - `resolve_session_user(session: Session, token: Optional[str]) -> Optional[User]`
  - `get_optional_user(request: Request, session: Session = Depends(get_session)) -> Optional[User]` (FastAPI dependency)
  - `require_user(user: Optional[User] = Depends(get_optional_user)) -> User` (raises 401 if `None`)
  - `require_admin(user: User = Depends(require_user)) -> User` (raises 403 if not `is_admin`)

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest backend/test_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.auth'`

- [ ] **Step 3: Write `backend/auth.py`**

```python
import os
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlmodel import Session

from backend.db import get_session
from backend.models import User

COOKIE_NAME = "ai_dash_session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 30  # 30 days, per AI-7's DoD

# Falls back to a fixed dev value so `import backend.main` and local dev
# work with zero setup — same pattern as DASHBOARD_PASSWORD defaulting to
# "" (auth disabled) in main.py. Production sets a real SESSION_SECRET via
# Terraform/Secret Manager (see infra/main.tf).
SESSION_SECRET = os.getenv("SESSION_SECRET", "dev-insecure-secret-change-in-prod")
_serializer = URLSafeTimedSerializer(SESSION_SECRET, salt="ai-dash-session")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def create_session_token(username: str) -> str:
    return _serializer.dumps({"username": username})


def verify_session_token(token: str) -> Optional[str]:
    try:
        data = _serializer.loads(token, max_age=SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    username = data.get("username") if isinstance(data, dict) else None
    return username if isinstance(username, str) else None


def resolve_session_user(session: Session, token: Optional[str]) -> Optional[User]:
    if not token:
        return None
    username = verify_session_token(token)
    if not username:
        return None
    return session.get(User, username)


def get_optional_user(request: Request, session: Session = Depends(get_session)) -> Optional[User]:
    return resolve_session_user(session, request.cookies.get(COOKIE_NAME))


def require_user(user: Optional[User] = Depends(get_optional_user)) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin required")
    return user
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest backend/test_auth.py -v`
Expected: `7 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/auth.py backend/test_auth.py
git commit -m "feat(AI-7): add password hashing, session tokens, and auth dependencies"
```

---

### Task 5: `backend/api/auth_routes.py` — login, logout, me, account management

**Files:**
- Create: `backend/api/auth_routes.py`
- Modify: `backend/main.py:10` (add router import), `backend/main.py:112` (add `app.include_router` call)
- Test: `backend/api/test_auth_routes.py`

**Interfaces:**
- Consumes: `backend.auth.{COOKIE_NAME, SESSION_MAX_AGE_SECONDS, create_session_token, hash_password, verify_password, require_user, require_admin, resolve_session_user}` (Task 4), `backend.models.User` (Task 3), `backend.db.get_session` (existing).
- Produces: `POST /api/login`, `POST /api/logout`, `GET /api/me`, `POST /api/accounts`, `GET /api/accounts`, `DELETE /api/accounts/{username}`, `PATCH /api/accounts/{username}`.

**Important design note on `/api/me`:** it must succeed (not 401) when no `User` rows exist yet — that's fallback/single-user-deploy mode, where there's no session concept at all, and the caller already passed Basic Auth to get this far (or Basic Auth is disabled entirely). In that mode it returns `{"username": null, "is_admin": false}`. Once at least one `User` exists, it requires a valid session cookie like every other protected route. Without this, `useMe()` on the frontend (Task 11) would 401 and bounce every fallback-mode deployment straight to `/login`, breaking today's "just works with a shared password" experience.

- [ ] **Step 1: Write the failing tests**

```python
from sqlmodel import Session, select

import backend.db as db_module
from backend.auth import hash_password
from backend.models import User


def _seed_user(username: str, password: str, is_admin: bool = False) -> None:
    with Session(db_module.engine) as session:
        session.add(User(username=username, password_hash=hash_password(password), is_admin=is_admin))
        session.commit()


def test_me_returns_null_username_when_no_accounts_exist(test_client):
    res = test_client.get("/api/me")
    assert res.status_code == 200
    assert res.json() == {"username": None, "is_admin": False}


def test_login_fails_with_wrong_password(test_client):
    _seed_user("alice", "correct-password")
    res = test_client.post("/api/login", json={"username": "alice", "password": "wrong"})
    assert res.status_code == 401


def test_login_fails_for_unknown_user(test_client):
    res = test_client.post("/api/login", json={"username": "nobody", "password": "x"})
    assert res.status_code == 401


def test_login_succeeds_and_sets_session_cookie(test_client):
    _seed_user("alice", "correct-password", is_admin=True)
    res = test_client.post("/api/login", json={"username": "alice", "password": "correct-password"})
    assert res.status_code == 200
    assert res.json() == {"username": "alice", "is_admin": True}
    assert "ai_dash_session" in res.cookies


def test_me_requires_session_once_an_account_exists(test_client):
    _seed_user("alice", "correct-password")
    res = test_client.get("/api/me")
    assert res.status_code == 401


def test_me_returns_identity_after_login(test_client):
    _seed_user("alice", "correct-password")
    test_client.post("/api/login", json={"username": "alice", "password": "correct-password"})
    res = test_client.get("/api/me")
    assert res.status_code == 200
    assert res.json() == {"username": "alice", "is_admin": False}


def test_logout_clears_session(test_client):
    _seed_user("alice", "correct-password")
    test_client.post("/api/login", json={"username": "alice", "password": "correct-password"})
    test_client.post("/api/logout")
    res = test_client.get("/api/me")
    assert res.status_code == 401


def test_bootstrap_creates_first_account_as_admin_with_no_auth(test_client):
    res = test_client.post("/api/accounts", json={"username": "gabby", "password": "hunter2"})
    assert res.status_code == 201
    assert res.json()["is_admin"] is True

    with Session(db_module.engine) as session:
        user = session.exec(select(User).where(User.username == "gabby")).one()
        assert user.is_admin is True


def test_create_account_after_bootstrap_requires_admin(test_client):
    test_client.post("/api/accounts", json={"username": "gabby", "password": "hunter2"})
    # No session cookie attached — a second creation attempt must be rejected.
    res = test_client.post("/api/accounts", json={"username": "bob", "password": "x"})
    assert res.status_code == 403


def test_create_account_as_admin_succeeds_and_is_not_admin_by_default(test_client):
    test_client.post("/api/accounts", json={"username": "gabby", "password": "hunter2"})
    test_client.post("/api/login", json={"username": "gabby", "password": "hunter2"})
    res = test_client.post("/api/accounts", json={"username": "bob", "password": "x"})
    assert res.status_code == 201
    assert res.json()["is_admin"] is False


def test_create_account_duplicate_username_conflicts(test_client):
    test_client.post("/api/accounts", json={"username": "gabby", "password": "hunter2"})
    test_client.post("/api/login", json={"username": "gabby", "password": "hunter2"})
    res = test_client.post("/api/accounts", json={"username": "gabby", "password": "x"})
    assert res.status_code == 409


def test_list_accounts_requires_admin(test_client):
    test_client.post("/api/accounts", json={"username": "gabby", "password": "hunter2"})
    test_client.post("/api/login", json={"username": "gabby", "password": "hunter2"})
    test_client.post("/api/accounts", json={"username": "bob", "password": "x"})
    res = test_client.get("/api/accounts")
    assert res.status_code == 200
    usernames = {a["username"] for a in res.json()}
    assert usernames == {"gabby", "bob"}


def test_delete_last_admin_is_blocked(test_client):
    test_client.post("/api/accounts", json={"username": "gabby", "password": "hunter2"})
    test_client.post("/api/login", json={"username": "gabby", "password": "hunter2"})
    res = test_client.delete("/api/accounts/gabby")
    assert res.status_code == 400


def test_demote_last_admin_is_blocked(test_client):
    test_client.post("/api/accounts", json={"username": "gabby", "password": "hunter2"})
    test_client.post("/api/login", json={"username": "gabby", "password": "hunter2"})
    res = test_client.patch("/api/accounts/gabby", json={"is_admin": False})
    assert res.status_code == 400


def test_delete_non_admin_account_succeeds(test_client):
    test_client.post("/api/accounts", json={"username": "gabby", "password": "hunter2"})
    test_client.post("/api/login", json={"username": "gabby", "password": "hunter2"})
    test_client.post("/api/accounts", json={"username": "bob", "password": "x"})
    res = test_client.delete("/api/accounts/bob")
    assert res.status_code == 200
    with Session(db_module.engine) as session:
        assert session.get(User, "bob") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest backend/api/test_auth_routes.py -v`
Expected: FAIL — 404s, since none of these routes exist yet.

- [ ] **Step 3: Write `backend/api/auth_routes.py`**

```python
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlmodel import Session, select

from backend.auth import (
    COOKIE_NAME,
    SESSION_MAX_AGE_SECONDS,
    create_session_token,
    hash_password,
    require_admin,
    resolve_session_user,
    verify_password,
)
from backend.db import get_session
from backend.models import User

router = APIRouter()


@router.post("/login")
def login(body: dict, response: Response, session: Session = Depends(get_session)):
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    user = session.get(User, username) if username else None
    if not user or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = create_session_token(user.username)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        # Modern Chrome/Firefox/Safari treat "localhost" as a secure
        # context, so a Secure cookie still round-trips in local dev over
        # plain http://localhost even though this is unconditionally True.
        # Hardcoding this avoids relying on X-Forwarded-Proto detection,
        # which Cloud Run's uvicorn process isn't currently configured to
        # trust (no --proxy-headers flag).
        secure=True,
        samesite="lax",
        path="/",
    )
    return {"username": user.username, "is_admin": user.is_admin}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(request: Request, session: Session = Depends(get_session)):
    any_user = session.exec(select(User.username).limit(1)).first() is not None
    if not any_user:
        # Fallback/single-user-deploy mode — no session concept applies.
        # Reaching this endpoint at all already means Basic Auth (if
        # configured) passed at the middleware layer.
        return {"username": None, "is_admin": False}
    user = resolve_session_user(session, request.cookies.get(COOKIE_NAME))
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {"username": user.username, "is_admin": user.is_admin}


def _admin_count(session: Session) -> int:
    return len(session.exec(select(User).where(User.is_admin == True)).all())  # noqa: E712


@router.post("/accounts", status_code=201)
def create_account(body: dict, request: Request, session: Session = Depends(get_session)):
    existing = session.exec(select(User)).all()
    if existing:
        # Not the bootstrap case — only an existing admin may create more
        # accounts. Bootstrap (no accounts yet) has no admin to check
        # against, so it's intentionally open here — in practice it's only
        # reachable while the dashboard is still gated by DASHBOARD_PASSWORD
        # Basic Auth at the middleware layer (Task 6).
        current = resolve_session_user(session, request.cookies.get(COOKIE_NAME))
        if current is None or not current.is_admin:
            raise HTTPException(status_code=403, detail="Admin required")

    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    if not username or not password:
        raise HTTPException(status_code=422, detail="username and password are required")
    if session.get(User, username):
        raise HTTPException(status_code=409, detail="Username already exists")

    user = User(username=username, password_hash=hash_password(password), is_admin=not existing)
    session.add(user)
    session.commit()
    session.refresh(user)
    return {"username": user.username, "is_admin": user.is_admin, "created_at": user.created_at}


@router.get("/accounts")
def list_accounts(current: User = Depends(require_admin), session: Session = Depends(get_session)):
    accounts = session.exec(select(User).order_by(User.created_at)).all()
    return [
        {"username": a.username, "is_admin": a.is_admin, "created_at": a.created_at}
        for a in accounts
    ]


@router.delete("/accounts/{username}")
def delete_account(username: str, current: User = Depends(require_admin), session: Session = Depends(get_session)):
    target = session.get(User, username)
    if not target:
        raise HTTPException(status_code=404, detail="Account not found")
    if target.is_admin and _admin_count(session) <= 1:
        raise HTTPException(status_code=400, detail="Cannot remove the last admin")
    session.delete(target)
    session.commit()
    return {"deleted": True}


@router.patch("/accounts/{username}")
def update_account(
    username: str,
    body: dict,
    current: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    target = session.get(User, username)
    if not target:
        raise HTTPException(status_code=404, detail="Account not found")
    if "is_admin" in body:
        new_value = bool(body["is_admin"])
        if target.is_admin and not new_value and _admin_count(session) <= 1:
            raise HTTPException(status_code=400, detail="Cannot demote the last admin")
        target.is_admin = new_value
        session.add(target)
        session.commit()
        session.refresh(target)
    return {"username": target.username, "is_admin": target.is_admin, "created_at": target.created_at}
```

- [ ] **Step 4: Wire the router into `backend/main.py`**

In `backend/main.py`, change line 10:

```python
from backend.api.routes import router
```
to:
```python
from backend.api.routes import router
from backend.api.auth_routes import router as auth_router
```

And change line 112:
```python
app.include_router(router, prefix="/api")
```
to:
```python
app.include_router(router, prefix="/api")
app.include_router(auth_router, prefix="/api")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest backend/api/test_auth_routes.py -v`
Expected: `15 passed`

- [ ] **Step 6: Commit**

```bash
git add backend/api/auth_routes.py backend/main.py backend/api/test_auth_routes.py
git commit -m "feat(AI-7): add login/logout/me and account management endpoints"
```

---

### Task 6: Rewrite the auth middleware in `backend/main.py`

**Files:**
- Modify: `backend/main.py:87-109`
- Test: `backend/test_middleware.py`

**Interfaces:**
- Consumes: `backend.auth.{COOKIE_NAME, resolve_session_user}` (Task 4), `backend.models.User` (Task 3).
- Produces: unauthenticated requests are blocked (`401` for `/api/*`, `302` redirect to `/login` for page routes) once any `User` account exists; unchanged Basic Auth fallback while none exist.

- [ ] **Step 1: Write the failing tests**

```python
import backend.main as main_module
from backend.auth import hash_password
from backend.models import User
from sqlmodel import Session
import backend.db as db_module


def test_fallback_mode_open_when_no_password_and_no_users(test_client, monkeypatch):
    monkeypatch.setattr(main_module, "_DASHBOARD_PASSWORD", "")
    res = test_client.get("/api/providers")
    assert res.status_code == 200


def test_fallback_mode_requires_basic_auth_password(test_client, monkeypatch):
    monkeypatch.setattr(main_module, "_DASHBOARD_PASSWORD", "secret")
    res = test_client.get("/api/providers")
    assert res.status_code == 401

    import base64

    good = base64.b64encode(b"ignored:secret").decode()
    res = test_client.get("/api/providers", headers={"Authorization": f"Basic {good}"})
    assert res.status_code == 200

    bad = base64.b64encode(b"ignored:wrong").decode()
    res = test_client.get("/api/providers", headers={"Authorization": f"Basic {bad}"})
    assert res.status_code == 401


def test_public_paths_bypass_auth_even_with_password_set(test_client, monkeypatch):
    monkeypatch.setattr(main_module, "_DASHBOARD_PASSWORD", "secret")
    assert test_client.get("/install.sh").status_code == 200
    assert test_client.get("/collector.py").status_code == 200


def test_session_mode_blocks_api_paths_without_cookie(test_client):
    with Session(db_module.engine) as session:
        session.add(User(username="alice", password_hash=hash_password("x")))
        session.commit()
    res = test_client.get("/api/providers")
    assert res.status_code == 401


def test_session_mode_redirects_page_paths_without_cookie(test_client):
    with Session(db_module.engine) as session:
        session.add(User(username="alice", password_hash=hash_password("x")))
        session.commit()
    res = test_client.get("/runs", follow_redirects=False)
    assert res.status_code == 302
    assert res.headers["location"] == "/login"


def test_session_mode_allows_api_paths_with_valid_cookie(test_client):
    with Session(db_module.engine) as session:
        session.add(User(username="alice", password_hash=hash_password("correct-password")))
        session.commit()
    test_client.post("/api/login", json={"username": "alice", "password": "correct-password"})
    res = test_client.get("/api/providers")
    assert res.status_code == 200


def test_session_mode_ignores_dashboard_password_once_a_user_exists(test_client, monkeypatch):
    monkeypatch.setattr(main_module, "_DASHBOARD_PASSWORD", "secret")
    with Session(db_module.engine) as session:
        session.add(User(username="alice", password_hash=hash_password("x")))
        session.commit()
    import base64

    good = base64.b64encode(b"ignored:secret").decode()
    res = test_client.get("/api/providers", headers={"Authorization": f"Basic {good}"})
    assert res.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest backend/test_middleware.py -v`
Expected: FAIL — session-mode tests get `200`/no redirect instead of `401`/`302`, since the middleware doesn't know about `User` rows yet.

- [ ] **Step 3: Rewrite the middleware**

In `backend/main.py`, replace lines 6-8 (imports) — add `RedirectResponse`:

```python
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
```

Replace lines 87-109 (the `_PUBLIC_PATHS` constant and `basic_auth` middleware) with:

```python
_PUBLIC_PATHS = frozenset({"/install.sh", "/collector.py", "/login", "/api/login"})


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    from sqlmodel import select
    from backend.auth import COOKIE_NAME, resolve_session_user
    from backend.db import get_session as _get_session
    from backend.models import User

    path = request.url.path
    # ingest has its own API key auth; the installer + collector download
    # routes, and the login page/endpoint, must be reachable with no
    # password or session at all.
    if path.startswith("/api/v1/ingest") or path in _PUBLIC_PATHS:
        return await call_next(request)

    with next(_get_session()) as session:
        any_user = session.exec(select(User.username).limit(1)).first() is not None

        if not any_user:
            # No accounts created yet — fall back to the shared-password
            # Basic Auth gate (today's single-user-deploy behavior),
            # byte-for-byte unchanged.
            if not _DASHBOARD_PASSWORD:
                return await call_next(request)
            auth = request.headers.get("Authorization", "")
            if auth.startswith("Basic "):
                try:
                    _, password = base64.b64decode(auth[6:]).decode().split(":", 1)
                    if password == _DASHBOARD_PASSWORD:
                        return await call_next(request)
                except Exception:
                    pass
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="ai-dash"'})

        # At least one account exists — Basic Auth is retired from here on;
        # only a valid session cookie gets through.
        user = resolve_session_user(session, request.cookies.get(COOKIE_NAME))

    if user is not None:
        return await call_next(request)
    if path.startswith("/api/"):
        return Response(status_code=401, content="Unauthorized")
    return RedirectResponse(url="/login", status_code=302)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest backend/test_middleware.py -v`
Expected: `7 passed`

- [ ] **Step 5: Run the full backend suite to check for regressions**

Run: `uv run pytest backend/ -v`
Expected: all tests pass, including Tasks 1-5's tests and the pre-existing `backend/api/test_routes.py` / `backend/adapters/test_*.py` files.

- [ ] **Step 6: Commit**

```bash
git add backend/main.py backend/test_middleware.py
git commit -m "feat(AI-7): auth middleware falls back to session cookies once an account exists"
```

---

### Task 7: Scope run data to the current user; admin-gate API key management

**Files:**
- Modify: `backend/api/routes.py`
- Test: `backend/api/test_scoping.py`

**Interfaces:**
- Consumes: `backend.auth.{get_optional_user, require_admin}` (Task 4), `backend.models.User` (Task 3).
- Produces: non-admin sessions see only their own `AgentRun` rows across `/api/runs`, `/api/runs/{id}`, `/api/stats`, `/api/daily`, `/api/users`; `/api/keys` (all methods) require `is_admin`.

- [ ] **Step 1: Write the failing tests**

```python
from sqlmodel import Session

import backend.db as db_module
from backend.auth import hash_password
from backend.models import AgentRun, User


def _seed_users_and_runs(session: Session):
    session.add(User(username="alice", password_hash=hash_password("x")))
    session.add(User(username="bob", password_hash=hash_password("y"), is_admin=True))
    session.add(AgentRun(id="run-alice", provider="anthropic", model="m", input_tokens=10, output_tokens=10, user="alice"))
    session.add(AgentRun(id="run-bob", provider="anthropic", model="m", input_tokens=10, output_tokens=10, user="bob"))
    session.commit()


def _login(client, username: str, password: str):
    res = client.post("/api/login", json={"username": username, "password": password})
    assert res.status_code == 200


def test_non_admin_sees_only_own_runs(test_client):
    with Session(db_module.engine) as session:
        _seed_users_and_runs(session)
    _login(test_client, "alice", "x")
    res = test_client.get("/api/runs")
    assert res.status_code == 200
    ids = {r["id"] for r in res.json()}
    assert ids == {"run-alice"}


def test_admin_sees_all_runs(test_client):
    with Session(db_module.engine) as session:
        _seed_users_and_runs(session)
    _login(test_client, "bob", "y")
    res = test_client.get("/api/runs")
    assert res.status_code == 200
    ids = {r["id"] for r in res.json()}
    assert ids == {"run-alice", "run-bob"}


def test_non_admin_cannot_view_others_run_detail(test_client):
    with Session(db_module.engine) as session:
        _seed_users_and_runs(session)
    _login(test_client, "alice", "x")
    res = test_client.get("/api/runs/run-bob")
    assert res.status_code == 404


def test_admin_can_view_any_run_detail(test_client):
    with Session(db_module.engine) as session:
        _seed_users_and_runs(session)
    _login(test_client, "bob", "y")
    res = test_client.get("/api/runs/run-alice")
    assert res.status_code == 200


def test_non_admin_users_endpoint_only_lists_self(test_client):
    with Session(db_module.engine) as session:
        _seed_users_and_runs(session)
    _login(test_client, "alice", "x")
    res = test_client.get("/api/users")
    assert res.json() == {"users": ["alice"]}


def test_non_admin_cannot_manage_api_keys(test_client):
    with Session(db_module.engine) as session:
        _seed_users_and_runs(session)
    _login(test_client, "alice", "x")
    assert test_client.get("/api/keys").status_code == 403
    assert test_client.post("/api/keys", json={"user": "alice"}).status_code == 403
    assert test_client.delete("/api/keys/adk_devkey_local").status_code == 403


def test_admin_can_manage_api_keys(test_client):
    with Session(db_module.engine) as session:
        _seed_users_and_runs(session)
    _login(test_client, "bob", "y")
    assert test_client.get("/api/keys").status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest backend/api/test_scoping.py -v`
Expected: FAIL — non-admin currently sees both runs; `/api/keys` currently has no auth gate at all.

- [ ] **Step 3: Modify `backend/api/routes.py`**

Add `User` and the auth dependencies to the imports (line 10 area):

```python
from backend.db import get_session
from backend.models import AgentRun, AgentRunRead, ApiKey, TranscriptStore, User
from backend.auth import get_optional_user, require_admin
```

Replace `_visible_runs` (lines 48-49) to accept an optional scoping user:

```python
def _visible_runs(session: Session, user: Optional[User] = None) -> list[AgentRun]:
    runs = session.exec(_visible_runs_query()).all()
    if user and not user.is_admin:
        runs = [r for r in runs if r.user == user.username]
    return runs
```

Update `list_runs` (lines 52-88) — add the `current_user` dependency and scope the query:

```python
@router.get("/runs", response_model=list[AgentRunRead])
def list_runs(
    provider: Optional[str] = None,
    status: Optional[str] = None,
    user: Optional[str] = None,
    ticket: Optional[str] = None,
    parent_id: Optional[str] = None,
    include_children: bool = False,
    limit: int = Query(50, le=500),
    offset: int = 0,
    current_user: Optional[User] = Depends(get_optional_user),
    session: Session = Depends(get_session),
):
    query = select(AgentRun).where(
        (AgentRun.input_tokens + AgentRun.output_tokens) > 0
    ).order_by(AgentRun.started_at.desc())
    if provider:
        query = query.where(AgentRun.provider == provider)
    if status:
        query = query.where(AgentRun.status == status)
    if user:
        query = query.where(AgentRun.user == user)
    if current_user and not current_user.is_admin:
        # Non-admins are always scoped to themselves, regardless of what
        # `user` was requested — this is the security boundary, not just
        # a default.
        query = query.where(AgentRun.user == current_user.username)
    if parent_id is not None:
        query = query.where(AgentRun.parent_id == parent_id)
    elif not include_children:
        query = query.where(AgentRun.parent_id == None)  # noqa: E711
    if ticket:
        ticket_lower = ticket.strip().lower()
        runs = [
            r for r in session.exec(query).all()
            if any(ticket_lower in ref.lower() for ref in r.ticket_refs)
        ]
        runs = runs[offset: offset + limit]
    else:
        runs = session.exec(query.offset(offset).limit(limit)).all()
    running_parents = _parents_with_running_children(session, [r.id for r in runs])
    return [_to_read(r, running_parents) for r in runs]
```

Update `get_run` (lines 91-96):

```python
@router.get("/runs/{run_id}", response_model=AgentRunRead)
def get_run(
    run_id: str,
    current_user: Optional[User] = Depends(get_optional_user),
    session: Session = Depends(get_session),
):
    run = session.get(AgentRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if current_user and not current_user.is_admin and run.user != current_user.username:
        raise HTTPException(status_code=404, detail="Run not found")
    return _to_read(run, _parents_with_running_children(session, [run_id]))
```

Update `list_providers` (lines 99-102):

```python
@router.get("/providers")
def list_providers(
    current_user: Optional[User] = Depends(get_optional_user),
    session: Session = Depends(get_session),
):
    runs = _visible_runs(session, current_user)
    return {"providers": list({r.provider for r in runs})}
```

Update `list_users` (lines 105-108):

```python
@router.get("/users")
def list_users(
    current_user: Optional[User] = Depends(get_optional_user),
    session: Session = Depends(get_session),
):
    runs = _visible_runs(session, current_user)
    return {"users": sorted({r.user for r in runs if r.user})}
```

Update `get_daily` (lines 111-137) — only the signature and the `_visible_runs` call change:

```python
@router.get("/daily")
def get_daily(
    user: Optional[str] = None,
    days: int = Query(7, ge=1, le=3650),
    current_user: Optional[User] = Depends(get_optional_user),
    session: Session = Depends(get_session),
):
    runs = _visible_runs(session, current_user)
    cutoff = datetime.utcnow() - timedelta(days=days)
    recent = [r for r in runs if r.started_at >= cutoff]
    if user:
        recent = [r for r in recent if r.user == user]
    buckets: dict[str, dict] = {}
    for i in range(days):
        day = datetime.utcnow() - timedelta(days=days - 1 - i)
        key = day.strftime("%Y-%m-%d")
        buckets[key] = {"date": day.strftime("%m/%d"), "anthropic": 0, "openai": 0, "gemini": 0,
                        "input_tokens": 0, "output_tokens": 0}
    for r in recent:
        key = r.started_at.strftime("%Y-%m-%d")
        if key in buckets:
            buckets[key][r.provider] = buckets[key].get(r.provider, 0) + 1
            buckets[key]["input_tokens"] += r.input_tokens
            buckets[key]["output_tokens"] += r.output_tokens
    return list(buckets.values())
```

Update `get_stats` (lines 140-170) — same pattern:

```python
@router.get("/stats")
def get_stats(
    user: Optional[str] = None,
    days: int = Query(7, ge=1, le=3650),
    current_user: Optional[User] = Depends(get_optional_user),
    session: Session = Depends(get_session),
):
    runs = _visible_runs(session, current_user)
    cutoff = datetime.utcnow() - timedelta(days=days)
    recent = [r for r in runs if r.started_at >= cutoff]
    if user:
        runs = [r for r in runs if r.user == user]
        recent = [r for r in recent if r.user == user]
    return {
        "total_runs_7d": len(recent),
        "total_input_tokens_7d": sum(r.input_tokens for r in recent),
        "total_output_tokens_7d": sum(r.output_tokens for r in recent),
        "total_commits_7d": sum(len(r.git_commits) for r in recent),
        "total_prs_7d": sum(len(r.git_prs) for r in recent),
        "days": days,
        "active_providers": list({r.provider for r in recent}),
        "running_count": sum(1 for r in runs if r.status == "running"),
        "by_provider": {
            p: {
                "runs": sum(1 for r in recent if r.provider == p),
                "input_tokens": sum(r.input_tokens for r in recent if r.provider == p),
                "output_tokens": sum(r.output_tokens for r in recent if r.provider == p),
                "commits": sum(len(r.git_commits) for r in recent if r.provider == p),
            }
            for p in PROVIDERS
        },
    }
```

Admin-gate the three `/keys` endpoints (lines 173-202) by adding `current: User = Depends(require_admin)`:

```python
@router.get("/keys")
def list_keys(current: User = Depends(require_admin), session: Session = Depends(get_session)):
    keys = session.exec(select(ApiKey).order_by(ApiKey.created_at.desc())).all()
    return [
        {"key_prefix": k.key[:12] + "…", "user": k.user, "created_at": k.created_at}
        for k in keys
    ]


@router.post("/keys", status_code=201)
def create_key(body: dict, current: User = Depends(require_admin), session: Session = Depends(get_session)):
    user = (body.get("user") or "").strip()
    if not user:
        raise HTTPException(status_code=422, detail="user is required")
    key = ApiKey(user=user)
    session.add(key)
    session.commit()
    session.refresh(key)
    return {"key": key.key, "user": key.user, "created_at": key.created_at}


@router.delete("/keys/{key_prefix}")
def delete_key(key_prefix: str, current: User = Depends(require_admin), session: Session = Depends(get_session)):
    keys = session.exec(select(ApiKey)).all()
    match = next((k for k in keys if k.key.startswith(key_prefix)), None)
    if not match:
        raise HTTPException(status_code=404, detail="Key not found")
    session.delete(match)
    session.commit()
    return {"deleted": True}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest backend/api/test_scoping.py -v`
Expected: `8 passed`

- [ ] **Step 5: Run the full backend suite to check for regressions**

Run: `uv run pytest backend/ -v`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/api/routes.py backend/api/test_scoping.py
git commit -m "feat(AI-7): scope run data to the current user, admin-gate API key management"
```

---

### Task 8: Terraform — `SESSION_SECRET` secret, and README auth docs

**Files:**
- Modify: `infra/variables.tf`
- Modify: `infra/main.tf:108-135` (secrets), `infra/main.tf:149-159` (IAM bindings), `infra/main.tf:203-224` (Cloud Run env vars)
- Modify: `README.md:124-129`

No automated test — this is infrastructure-as-code. Verification is `terraform validate`/`terraform plan`, not `terraform apply` (applying is a production change the user should run and review themselves).

- [ ] **Step 1: Add the `session_secret` variable**

In `infra/variables.tf`, add after the `dashboard_password` variable:

```hcl
variable "session_secret" {
  description = "Secret key used to sign per-user login session cookies"
  type        = string
  sensitive   = true
}
```

- [ ] **Step 2: Add the Secret Manager secret**

In `infra/main.tf`, add after the `dashboard_password` secret block (after line 134):

```hcl
resource "google_secret_manager_secret" "session_secret" {
  secret_id = "ai-dash-session-secret"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "session_secret" {
  secret      = google_secret_manager_secret.session_secret.id
  secret_data = var.session_secret
}
```

- [ ] **Step 3: Grant the app service account access to it**

Add after the `dashboard_password` IAM binding (after line 159):

```hcl
resource "google_secret_manager_secret_iam_member" "session_secret" {
  secret_id = google_secret_manager_secret.session_secret.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.app.email}"
}
```

- [ ] **Step 4: Wire it into the Cloud Run service's env vars**

In the `containers` block, add after the `DASHBOARD_PASSWORD` `env` block (after line 224):

```hcl
      env {
        name = "SESSION_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.session_secret.secret_id
            version = "latest"
          }
        }
      }
```

- [ ] **Step 5: Update the README's Auth section**

Replace the `## Auth` section (`README.md:124-129`) with:

```markdown
## Auth

New deployments start password-protected: the dashboard is gated by HTTP Basic Auth, using the
`DASHBOARD_PASSWORD` env var (stored in GCP Secret Manager, set via `terraform.tfvars`). Username
is ignored — only the password is checked.

As soon as the first user account is created (Settings → Users), Basic Auth is retired for that
deployment and only per-user login (`/login`, session cookie signed with `SESSION_SECRET`, 30-day
expiry) works from then on. This is a one-way cutover: anyone who created that first account will
need to log in with it explicitly — their browser's cached Basic Auth credentials stop working on
the very next request.

Non-admin users only see their own runs. Admins see everyone's runs and can create/revoke
accounts and API keys from Settings.

API ingest requires an `X-API-Key` header. Keys are seeded in the DB on first startup
(`adk_devkey_local` for local dev) and are managed from Settings by admins.
```

- [ ] **Step 6: Validate the Terraform changes**

Run: `cd infra && terraform validate`
Expected: `Success! The configuration is valid.`

Do **not** run `terraform apply` as part of this task — that provisions real GCP resources and secrets. Surface `terraform plan` output for the user to review, and let them apply it explicitly once they've set a real `session_secret` value in their `terraform.tfvars`.

- [ ] **Step 7: Commit**

```bash
git add infra/variables.tf infra/main.tf README.md
git commit -m "infra(AI-7): add SESSION_SECRET for per-user login sessions"
```

---

### Task 9: Frontend types — `Me`

**Files:**
- Modify: `frontend/src/lib/types.ts`

**Interfaces:**
- Produces: `Me` interface — `{ username: string | null; is_admin: boolean }`.

- [ ] **Step 1: Add the interface**

In `frontend/src/lib/types.ts`, add at the end of the file:

```typescript
export interface Me {
  username: string | null;
  is_admin: boolean;
}
```

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors (this is an additive, unused-so-far export).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/types.ts
git commit -m "feat(AI-7): add Me type"
```

---

### Task 10: `frontend/src/lib/api.ts` — session-aware fetch, `useMe`, `login`, `logout`

**Files:**
- Modify: `frontend/src/lib/api.ts`

**Interfaces:**
- Consumes: `Me` type (Task 9).
- Produces: `useMe(): UseQueryResult<Me>`, `login(username: string, password: string): Promise<void>`, `logout(): Promise<void>`. Existing `get<T>` now redirects to `/login` on any `401`.

- [ ] **Step 1: Update the file**

In `frontend/src/lib/api.ts`, change line 2:

```typescript
import type { AgentRun, Stats, DailyBucket, Me } from "./types";
```

Replace the `get<T>` function (lines 6-10):

```typescript
async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (res.status === 401) {
    window.location.href = "/login";
    throw new Error("401 Unauthorized");
  }
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}
```

Add at the end of the file:

```typescript

export function useMe() {
  return useQuery<Me>({
    queryKey: ["me"],
    queryFn: () => get("/me"),
    retry: false,
  });
}

export async function login(username: string, password: string): Promise<void> {
  const res = await fetch(`${BASE}/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail ?? "Invalid username or password");
  }
}

export async function logout(): Promise<void> {
  await fetch(`${BASE}/logout`, { method: "POST" });
  window.location.href = "/login";
}
```

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/api.ts
git commit -m "feat(AI-7): add useMe/login/logout, redirect to /login on 401"
```

---

### Task 11: `/login` page

**Files:**
- Create: `frontend/src/pages/Login.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `login` (Task 10).
- Produces: `Login` component, mounted at route `/login` (outside `<Layout>`).

- [ ] **Step 1: Write `frontend/src/pages/Login.tsx`**

```tsx
import { useState, type FormEvent } from "react";
import { login } from "../lib/api";

export function Login() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await login(username, password);
      window.location.href = "/";
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-950">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-xs bg-slate-900 border border-slate-800 rounded-lg p-6 space-y-4"
      >
        <div>
          <h1 className="text-sm font-mono font-semibold text-slate-100 tracking-wide">ai_dash</h1>
          <p className="text-xs text-slate-500 mt-1">Sign in to continue</p>
        </div>
        <div className="space-y-1">
          <label className="text-xs text-slate-500 font-mono block">username</label>
          <input
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoFocus
            className="w-full bg-slate-800 border border-slate-700 rounded px-3 py-1.5 text-sm text-slate-200 font-mono focus:outline-none focus:border-slate-500"
          />
        </div>
        <div className="space-y-1">
          <label className="text-xs text-slate-500 font-mono block">password</label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full bg-slate-800 border border-slate-700 rounded px-3 py-1.5 text-sm text-slate-200 font-mono focus:outline-none focus:border-slate-500"
          />
        </div>
        {error && <p className="text-xs text-red-400">{error}</p>}
        <button
          type="submit"
          disabled={loading || !username.trim() || !password}
          className="w-full px-3 py-1.5 rounded text-sm bg-slate-700 text-slate-200 hover:bg-slate-600 disabled:opacity-40 disabled:cursor-not-allowed transition-colors font-mono"
        >
          {loading ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
```

- [ ] **Step 2: Add the route in `frontend/src/App.tsx`**

Change line 7 (add the import):

```tsx
import { Settings } from "./pages/Settings";
import { Login } from "./pages/Login";
```

Change the `<Routes>` block (lines 17-24) to add the `/login` route outside `<Layout>`:

```tsx
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route element={<Layout />}>
          <Route path="/" element={<Dashboard />} />
          <Route path="/runs" element={<Runs />} />
          <Route path="/runs/:id" element={<RunDetail />} />
          <Route path="/settings" element={<Settings />} />
        </Route>
      </Routes>
```

Leave `<UserProvider>` in place for now — `Layout.tsx`, `Runs.tsx`, and `Dashboard.tsx` still depend on it until Tasks 12 and 13 remove those usages.

- [ ] **Step 3: Typecheck and manually verify**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

Manual verification: with both `uvicorn backend.main:app --reload` and `npm run dev` running, visit `http://localhost:5173/login` — the form renders. Submitting doesn't need to work yet (no accounts exist in a fresh DB, and this task doesn't touch that flow).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/Login.tsx frontend/src/App.tsx
git commit -m "feat(AI-7): add /login page"
```

---

### Task 12: `Layout.tsx` — remove the global user switcher, add identity + logout

**Files:**
- Modify: `frontend/src/components/Layout.tsx`

**Interfaces:**
- Consumes: `useMe`, `logout` (Task 10).

- [ ] **Step 1: Rewrite the file**

```tsx
import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { useMe, logout } from "../lib/api";

const nav = [
  { to: "/", label: "Overview", icon: "⬡" },
  { to: "/runs", label: "Runs", icon: "▶" },
  { to: "/settings", label: "Settings", icon: "⚙" },
];

export function Layout() {
  const { data: me } = useMe();
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  return (
    <div className="flex h-screen overflow-hidden">
      <aside
        className={`fixed inset-y-0 left-0 z-40 w-52 shrink-0 bg-slate-900 border-r border-slate-800 flex flex-col transform transition-transform duration-200 md:relative md:translate-x-0 md:z-auto ${
          mobileNavOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="px-5 py-4 border-b border-slate-800">
          <span className="text-sm font-mono font-semibold text-slate-100 tracking-wide">ai_dash</span>
          <span className="ml-2 text-xs text-slate-500">v0.1</span>
        </div>
        {me?.username && (
          <div className="px-3 py-2.5 border-b border-slate-800 flex items-center justify-between">
            <span className="text-xs text-slate-400 font-mono truncate">{me.username}</span>
            <button
              type="button"
              onClick={() => logout()}
              className="text-xs text-slate-500 hover:text-slate-300 font-mono"
            >
              logout
            </button>
          </div>
        )}
        <nav className="flex-1 px-2 py-3 space-y-0.5">
          {nav.map(({ to, label, icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              onClick={() => setMobileNavOpen(false)}
              className={({ isActive }) =>
                `flex items-center gap-2.5 px-3 py-2 rounded-md text-sm transition-colors ${
                  isActive
                    ? "bg-slate-800 text-slate-100"
                    : "text-slate-400 hover:text-slate-200 hover:bg-slate-800/50"
                }`
              }
            >
              <span className="text-xs opacity-60">{icon}</span>
              {label}
            </NavLink>
          ))}
        </nav>
      </aside>

      {mobileNavOpen && (
        <div
          className="fixed inset-0 bg-black/50 z-30 md:hidden"
          onClick={() => setMobileNavOpen(false)}
        />
      )}

      <div className="flex-1 flex flex-col overflow-hidden">
        <header className="md:hidden sticky top-0 z-10 flex items-center gap-3 px-4 py-3 bg-slate-900 border-b border-slate-800">
          <button
            type="button"
            onClick={() => setMobileNavOpen(true)}
            aria-label="Open navigation"
            className="text-slate-300 text-xl leading-none"
          >
            ☰
          </button>
          <span className="text-sm font-mono font-semibold text-slate-100 tracking-wide">ai_dash</span>
        </header>
        <main className="flex-1 overflow-auto">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors. (`Runs.tsx` and `Dashboard.tsx` still import `useActiveUser` from `UserContext.tsx`, which still exists and is still provided by `<UserProvider>` in `App.tsx` — this task doesn't touch either, so nothing breaks.)

- [ ] **Step 3: Manual verification**

With a fresh local DB (delete `ai_dash.db` or use a clean Postgres) and no `DASHBOARD_PASSWORD` set, run the backend and frontend. Visit `http://localhost:5173/` — the sidebar no longer shows the "user:" dropdown. No username/logout row appears either (fallback mode, `me.username` is `null`).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/Layout.tsx
git commit -m "feat(AI-7): replace global user switcher with session identity + logout"
```

---

### Task 13: `Settings.tsx` — Users (accounts) admin section, admin-gated API Keys section

**Files:**
- Modify: `frontend/src/pages/Settings.tsx`

**Interfaces:**
- Consumes: `useMe` (Task 10), backend `/api/accounts` endpoints (Task 5), backend `/api/keys` endpoints (now admin-gated, Task 7).

- [ ] **Step 1: Rewrite the file**

```tsx
import { useEffect, useState } from "react";
import { useMe } from "../lib/api";

interface KeyEntry {
  key_prefix: string;
  user: string;
  created_at: string;
}

interface AccountEntry {
  username: string;
  is_admin: boolean;
  created_at: string;
}

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString("en-US", {
    month: "short", day: "numeric", year: "numeric",
  });
}

function UsersSection({ isBootstrap, isAdmin }: { isBootstrap: boolean; isAdmin: boolean }) {
  const [accounts, setAccounts] = useState<AccountEntry[]>([]);
  const [newUsername, setNewUsername] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [bootstrapped, setBootstrapped] = useState(false);

  useEffect(() => {
    if (isBootstrap) return;
    fetch("/api/accounts")
      .then(async (res) => {
        const data = await res.json();
        if (!res.ok) {
          setError(data.detail ?? "Failed to load accounts");
          return;
        }
        setAccounts(Array.isArray(data) ? data : []);
      })
      .catch(() => setError("Failed to load accounts"));
  }, [isBootstrap]);

  async function handleCreate() {
    const username = newUsername.trim();
    if (!username || !newPassword) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/accounts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password: newPassword }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.detail ?? "Failed to create account");
        return;
      }
      if (isBootstrap) {
        setBootstrapped(true);
        return;
      }
      setAccounts((prev) => [...prev, data]);
      setNewUsername("");
      setNewPassword("");
    } catch {
      setError("Failed to create account");
    } finally {
      setLoading(false);
    }
  }

  async function handleDelete(username: string) {
    if (!window.confirm(`Delete account for ${username}?`)) return;
    try {
      const res = await fetch(`/api/accounts/${username}`, { method: "DELETE" });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError(data.detail ?? "Failed to delete account");
        return;
      }
      setAccounts((prev) => prev.filter((a) => a.username !== username));
    } catch {
      setError("Failed to delete account");
    }
  }

  async function handleToggleAdmin(account: AccountEntry) {
    try {
      const res = await fetch(`/api/accounts/${account.username}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ is_admin: !account.is_admin }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.detail ?? "Failed to update account");
        return;
      }
      setAccounts((prev) => prev.map((a) => (a.username === account.username ? data : a)));
    } catch {
      setError("Failed to update account");
    }
  }

  if (isBootstrap && bootstrapped) {
    return (
      <section>
        <h2 className="text-xs text-slate-500 font-mono uppercase tracking-wider mb-3">Users</h2>
        <div className="bg-slate-800 border border-emerald-500/30 text-emerald-300 font-mono text-xs p-3 rounded">
          Account created. The shared dashboard password no longer works — sign in with this
          account to continue.
        </div>
        <button
          onClick={() => { window.location.href = "/login"; }}
          className="mt-3 px-3 py-1.5 rounded text-sm bg-slate-700 text-slate-200 hover:bg-slate-600 transition-colors font-mono"
        >
          Go to login
        </button>
      </section>
    );
  }

  return (
    <section>
      <h2 className="text-xs text-slate-500 font-mono uppercase tracking-wider mb-3">Users</h2>

      {(isBootstrap || isAdmin) && (
        <div className="flex gap-2 mb-4">
          <input
            type="text"
            placeholder="username"
            value={newUsername}
            onChange={(e) => setNewUsername(e.target.value)}
            className="flex-1 bg-slate-800 border border-slate-700 rounded px-3 py-1.5 text-sm text-slate-200 placeholder-slate-500 font-mono focus:outline-none focus:border-slate-500"
          />
          <input
            type="password"
            placeholder="password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleCreate()}
            className="flex-1 bg-slate-800 border border-slate-700 rounded px-3 py-1.5 text-sm text-slate-200 placeholder-slate-500 font-mono focus:outline-none focus:border-slate-500"
          />
          <button
            onClick={handleCreate}
            disabled={loading || !newUsername.trim() || !newPassword}
            className="px-3 py-1.5 rounded text-sm bg-slate-700 text-slate-200 hover:bg-slate-600 disabled:opacity-40 disabled:cursor-not-allowed transition-colors font-mono"
          >
            Create Account
          </button>
        </div>
      )}

      {error && <p className="text-xs text-red-400 mb-3">{error}</p>}

      {!isBootstrap && (
        <div className="border border-slate-800 rounded overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[480px] text-sm font-mono">
              <thead>
                <tr className="border-b border-slate-800 text-xs text-slate-500 uppercase tracking-wider">
                  <th className="px-4 py-2 text-left font-normal">Username</th>
                  <th className="px-4 py-2 text-left font-normal">Admin</th>
                  <th className="px-4 py-2 text-left font-normal">Created</th>
                  <th className="px-4 py-2 text-left font-normal"></th>
                </tr>
              </thead>
              <tbody>
                {accounts.length === 0 ? (
                  <tr>
                    <td colSpan={4} className="px-4 py-4 text-center text-slate-600 text-xs">No accounts yet</td>
                  </tr>
                ) : (
                  accounts.map((a) => (
                    <tr key={a.username} className="border-b border-slate-800/50 last:border-0 hover:bg-slate-800/30">
                      <td className="px-4 py-2.5 text-slate-300">{a.username}</td>
                      <td className="px-4 py-2.5">
                        <button
                          onClick={() => handleToggleAdmin(a)}
                          className={a.is_admin ? "text-emerald-400" : "text-slate-600 hover:text-slate-400"}
                        >
                          {a.is_admin ? "admin" : "member"}
                        </button>
                      </td>
                      <td className="px-4 py-2.5 text-slate-500">{formatDate(a.created_at)}</td>
                      <td className="px-4 py-2.5 text-right">
                        <button
                          onClick={() => handleDelete(a.username)}
                          className="text-slate-600 hover:text-red-400 transition-colors px-1"
                          title="Delete account"
                        >
                          ×
                        </button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  );
}

function ApiKeysSection() {
  const [keys, setKeys] = useState<KeyEntry[]>([]);
  const [newUser, setNewUser] = useState("");
  const [createdKey, setCreatedKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    fetch("/api/keys")
      .then(async (res) => {
        const data = await res.json();
        if (!res.ok) {
          setError(data.detail ?? "Failed to load keys");
          return;
        }
        setKeys(Array.isArray(data) ? data : []);
      })
      .catch(() => setError("Failed to load keys"));
  }, []);

  async function handleCreate() {
    const user = newUser.trim();
    if (!user) return;
    setLoading(true);
    setError(null);
    setCreatedKey(null);
    try {
      const res = await fetch("/api/keys", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user }),
      });
      if (!res.ok) {
        const data = await res.json();
        setError(data.detail ?? "Failed to create key");
        return;
      }
      const data = await res.json();
      setCreatedKey(data.key);
      setKeys((prev) => [
        { key_prefix: data.key.slice(0, 12) + "…", user: data.user, created_at: data.created_at },
        ...prev,
      ]);
      setNewUser("");
    } catch {
      setError("Failed to create key");
    } finally {
      setLoading(false);
    }
  }

  async function handleDelete(entry: KeyEntry) {
    if (!window.confirm(`Delete key for ${entry.user}?`)) return;
    const prefix = entry.key_prefix.replace("…", "");
    try {
      const res = await fetch(`/api/keys/${prefix}`, { method: "DELETE" });
      if (!res.ok) {
        setError("Failed to delete key");
        return;
      }
      setKeys((prev) => prev.filter((k) => k.key_prefix !== entry.key_prefix));
      if (createdKey && createdKey.startsWith(prefix)) setCreatedKey(null);
    } catch {
      setError("Failed to delete key");
    }
  }

  return (
    <section>
      <h2 className="text-xs text-slate-500 font-mono uppercase tracking-wider mb-3">API Keys</h2>

      <div className="flex gap-2 mb-4">
        <input
          type="text"
          placeholder="username"
          value={newUser}
          onChange={(e) => setNewUser(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleCreate()}
          className="flex-1 bg-slate-800 border border-slate-700 rounded px-3 py-1.5 text-sm text-slate-200 placeholder-slate-500 font-mono focus:outline-none focus:border-slate-500"
        />
        <button
          onClick={handleCreate}
          disabled={loading || !newUser.trim()}
          className="px-3 py-1.5 rounded text-sm bg-slate-700 text-slate-200 hover:bg-slate-600 disabled:opacity-40 disabled:cursor-not-allowed transition-colors font-mono"
        >
          Create Key
        </button>
      </div>

      {error && <p className="text-xs text-red-400 mb-3">{error}</p>}

      {createdKey && (
        <div className="mb-4 bg-slate-800 border border-emerald-500/30 text-emerald-300 font-mono text-xs p-3 rounded">
          <div className="flex items-center justify-between gap-3">
            <span className="break-all">{createdKey}</span>
            <button
              onClick={() => navigator.clipboard.writeText(createdKey)}
              className="shrink-0 px-2 py-1 rounded bg-emerald-900/40 hover:bg-emerald-900/70 transition-colors"
            >
              Copy
            </button>
          </div>
          <p className="mt-2 text-emerald-500/70">This key is only shown once.</p>
        </div>
      )}

      <div className="border border-slate-800 rounded overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[480px] text-sm font-mono">
          <thead>
            <tr className="border-b border-slate-800 text-xs text-slate-500 uppercase tracking-wider">
              <th className="px-4 py-2 text-left font-normal">Key Prefix</th>
              <th className="px-4 py-2 text-left font-normal">User</th>
              <th className="px-4 py-2 text-left font-normal">Created</th>
              <th className="px-4 py-2 text-left font-normal"></th>
            </tr>
          </thead>
          <tbody>
            {keys.length === 0 ? (
              <tr>
                <td colSpan={4} className="px-4 py-4 text-center text-slate-600 text-xs">No keys yet</td>
              </tr>
            ) : (
              keys.map((k) => (
                <tr key={k.key_prefix} className="border-b border-slate-800/50 last:border-0 hover:bg-slate-800/30">
                  <td className="px-4 py-2.5 text-slate-400">{k.key_prefix}</td>
                  <td className="px-4 py-2.5 text-slate-300">{k.user}</td>
                  <td className="px-4 py-2.5 text-slate-500">{formatDate(k.created_at)}</td>
                  <td className="px-4 py-2.5 text-right">
                    <button
                      onClick={() => handleDelete(k)}
                      className="text-slate-600 hover:text-red-400 transition-colors px-1"
                      title="Delete key"
                    >
                      ×
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}

export function Settings() {
  const { data: me } = useMe();
  const isBootstrap = !!me && me.username === null;
  const isAdmin = me?.is_admin ?? false;

  return (
    <div className="p-6 max-w-2xl space-y-8">
      <h1 className="text-lg font-mono font-semibold text-slate-100">Settings</h1>

      <UsersSection isBootstrap={isBootstrap} isAdmin={isAdmin} />

      {isAdmin && <ApiKeysSection />}

      {!isBootstrap && !isAdmin && (
        <p className="text-sm text-slate-500">Only admins can manage users and API keys.</p>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Manual verification (full bootstrap flow)**

With a fresh local DB and `DASHBOARD_PASSWORD=testpass` set:
1. Visit `http://localhost:5173/settings` — browser's native Basic Auth prompt appears (unchanged fallback behavior); authenticate with `testpass`.
2. The "Users" section shows a create form and no table (bootstrap mode). Create an account (e.g. `gabby` / `hunter2`). A message appears: "Account created… sign in with this account to continue," with a "Go to login" button.
3. Click it — lands on `/login`. Sign in as `gabby`/`hunter2`.
4. Redirected to `/`. Sidebar now shows `gabby` + `logout`. Visit `/settings` again — "Users" now shows a table with `gabby` marked `admin`, plus the "API Keys" section (visible because `gabby` is admin).
5. Try deleting or demoting `gabby` from the table — both are blocked (last-admin guard), matching Task 5/7's backend tests.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/Settings.tsx
git commit -m "feat(AI-7): add Users admin section, gate API Keys section on admin"
```

---

### Task 14: `Runs.tsx` + `Dashboard.tsx` — drop the global user context; admin "view as" filter

This is the last consumer of `UserContext.tsx` — this task also removes `<UserProvider>` from `App.tsx` and deletes the file.

**Files:**
- Modify: `frontend/src/pages/Runs.tsx`
- Modify: `frontend/src/pages/Dashboard.tsx`
- Modify: `frontend/src/App.tsx`
- Delete: `frontend/src/lib/UserContext.tsx`

**Interfaces:**
- Consumes: `useMe` (Task 10).

- [ ] **Step 1: Rewrite `frontend/src/pages/Runs.tsx`**

```tsx
import { useEffect, useState } from "react";
import { useRuns, useUsers, useMe } from "../lib/api";
import { fmt, duration } from "../lib/format";
import { StatusBadge } from "../components/StatusBadge";
import { ProviderBadge } from "../components/ProviderBadge";
import { useNavigate } from "react-router-dom";
import { ticketUrl, prLabel, commitUrl, repoBase } from "../lib/links";

const PAGE_SIZE = 50;

export function Runs() {
  const [provider, setProvider] = useState("");
  const [status, setStatus] = useState("");
  const [user, setUser] = useState("");
  const [ticket, setTicket] = useState("");
  const [page, setPage] = useState(0);
  const { data: me } = useMe();
  const { data: usersData } = useUsers();
  const { data: runs, isLoading } = useRuns({
    provider: provider || undefined,
    status: status || undefined,
    user: user || undefined,
    ticket: ticket || undefined,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  });
  const navigate = useNavigate();

  // Reset to the first page whenever any filter changes.
  useEffect(() => {
    setPage(0);
  }, [provider, status, user, ticket]);

  // No total-count endpoint exists, so infer "more pages" from a full page
  // coming back — a short page means this was the last one.
  const hasNextPage = (runs?.length ?? 0) >= PAGE_SIZE;

  return (
    <div className="p-6 space-y-5">
      <div>
        <h1 className="text-lg font-mono font-semibold text-slate-100">Runs</h1>
        <p className="text-sm text-slate-500 mt-0.5">
          Showing {runs?.length ?? 0} results (page {page + 1})
        </p>
      </div>

      <div className="flex flex-wrap gap-2">
        {[
          { label: "Provider", value: provider, set: setProvider, options: ["", "anthropic", "openai", "gemini"] },
          { label: "Status", value: status, set: setStatus, options: ["", "running", "done", "failed"] },
        ].map(({ label, value, set, options }) => (
          <select
            key={label}
            value={value}
            onChange={(e) => set(e.target.value)}
            className="bg-slate-900 border border-slate-700 text-slate-300 text-sm rounded px-3 py-1.5 font-mono focus:outline-none focus:border-slate-500"
          >
            {options.map((o) => (
              <option key={o} value={o}>{o || label}</option>
            ))}
          </select>
        ))}
        {me?.is_admin && (
          <select
            value={user}
            onChange={(e) => setUser(e.target.value)}
            className="bg-slate-900 border border-slate-700 text-slate-300 text-sm rounded px-3 py-1.5 font-mono focus:outline-none focus:border-slate-500"
          >
            <option value="">User</option>
            {usersData?.users.map((u) => (
              <option key={u} value={u}>{u}</option>
            ))}
          </select>
        )}
        <input
          placeholder="Ticket (e.g. LINEAR-123)"
          value={ticket}
          onChange={(e) => setTicket(e.target.value)}
          className="bg-slate-900 border border-slate-700 text-slate-300 text-sm rounded px-3 py-1.5 font-mono focus:outline-none focus:border-slate-500 w-48"
        />
      </div>

      <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-sm">
          <thead>
            <tr className="border-b border-slate-800">
              {["Task", "Provider", "Model", "User", "Status", "Duration", "Tokens", "Ticket", "Code"].map((h) => (
                <th key={h} className="text-left px-4 py-2.5 text-xs font-mono text-slate-500 font-normal whitespace-nowrap">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr><td colSpan={9} className="px-4 py-8 text-center text-slate-600 font-mono text-sm">loading…</td></tr>
            )}
            {runs?.map((run) => (
              <tr
                key={run.id}
                onClick={() => navigate(`/runs/${run.id}`)}
                className="border-b border-slate-800/50 hover:bg-slate-800/40 cursor-pointer transition-colors"
              >
                <td className="px-4 py-3 max-w-xs">
                  <p className="text-slate-200 truncate">{run.label}</p>
                </td>
                <td className="px-4 py-3"><ProviderBadge provider={run.provider} /></td>
                <td className="px-4 py-3 text-slate-400 font-mono text-xs whitespace-nowrap">{run.model}</td>
                <td className="px-4 py-3 text-slate-400 font-mono text-xs">{run.user ?? "—"}</td>
                <td className="px-4 py-3"><StatusBadge status={run.status} /></td>
                <td className="px-4 py-3 text-slate-400 font-mono text-xs whitespace-nowrap">{duration(run.duration_seconds)}</td>
                <td className="px-4 py-3 text-slate-400 font-mono text-xs whitespace-nowrap">{fmt(run.input_tokens + run.output_tokens)}</td>
                <td className="px-4 py-3 text-xs font-mono">
                  {run.ticket_refs.length > 1
                    ? <span className="text-violet-400">{run.ticket_refs.length} tickets</span>
                    : run.ticket_refs[0]
                    ? (() => {
                        const url = ticketUrl(run.ticket_refs[0]);
                        return url
                          ? <a href={url} target="_blank" rel="noopener noreferrer"
                              onClick={(e) => e.stopPropagation()}
                              className="text-violet-400 hover:underline">{run.ticket_refs[0]}</a>
                          : <span className="text-violet-400">{run.ticket_refs[0]}</span>;
                      })()
                    : <span className="text-slate-600">—</span>}
                </td>
                <td className="px-4 py-3 text-xs font-mono">
                  {run.git_prs.length > 1
                    ? (() => {
                        const base = repoBase(run.git_prs[0]);
                        const label = `${run.git_prs.length} PRs · ${run.git_commits.length} commits`;
                        return base
                          ? <a href={`${base}/pulls`} target="_blank" rel="noopener noreferrer"
                              onClick={(e) => e.stopPropagation()}
                              className="text-blue-400 hover:underline">{label}</a>
                          : <span className="text-slate-300">{label}</span>;
                      })()
                    : run.git_prs.length > 0
                    ? <a href={run.git_prs[0]} target="_blank" rel="noopener noreferrer"
                        onClick={(e) => e.stopPropagation()}
                        className="text-blue-400 hover:underline">
                        {prLabel(run.git_prs[0])}
                        {run.git_commits.length > 1 && <span className="text-slate-500 ml-1">({run.git_commits.length} commits)</span>}
                      </a>
                    : run.git_commits.length > 0
                    ? (() => {
                        const hash = run.git_commits[0];
                        const url = commitUrl(hash, run.meta?.github_repo, run.git_prs);
                        const label = hash.slice(0, 7);
                        const extra = run.git_commits.length > 1 && <span className="text-slate-500 ml-1">+{run.git_commits.length - 1}</span>;
                        return url
                          ? <a href={url} target="_blank" rel="noopener noreferrer"
                              onClick={(e) => e.stopPropagation()}
                              className="text-blue-400 hover:underline">{label}{extra}</a>
                          : <span className="text-slate-300">{label}{extra}</span>;
                      })()
                    : <span className="text-slate-600">—</span>}
                </td>
              </tr>
            ))}
          </tbody>
          </table>
        </div>
      </div>

      <div className="flex items-center justify-end gap-2">
        <button
          type="button"
          onClick={() => setPage((p) => Math.max(0, p - 1))}
          disabled={page === 0}
          className="bg-slate-900 border border-slate-700 text-slate-300 text-sm rounded px-3 py-1.5 font-mono disabled:opacity-40 disabled:cursor-not-allowed hover:enabled:border-slate-500"
        >
          Previous
        </button>
        <button
          type="button"
          onClick={() => setPage((p) => p + 1)}
          disabled={!hasNextPage}
          className="bg-slate-900 border border-slate-700 text-slate-300 text-sm rounded px-3 py-1.5 font-mono disabled:opacity-40 disabled:cursor-not-allowed hover:enabled:border-slate-500"
        >
          Next
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Update `frontend/src/pages/Dashboard.tsx`**

Remove the `useActiveUser` import (line 3) and its usage. Change:

```tsx
import { useState } from "react";
import { useStats, useDaily } from "../lib/api";
import { useActiveUser } from "../lib/UserContext";
import { fmt } from "../lib/format";
```
to:
```tsx
import { useState } from "react";
import { useStats, useDaily } from "../lib/api";
import { fmt } from "../lib/format";
```

Change:
```tsx
export function Dashboard() {
  const { user } = useActiveUser();
  const [days, setDays] = useState(7);
  const { data: stats } = useStats(user || undefined, days);
  const { data: daily } = useDaily(user || undefined, days);
```
to:
```tsx
export function Dashboard() {
  const [days, setDays] = useState(7);
  const { data: stats } = useStats(undefined, days);
  const { data: daily } = useDaily(undefined, days);
```

The rest of `Dashboard.tsx` is unchanged.

- [ ] **Step 3: Update `frontend/src/App.tsx`**

Remove the `UserProvider` import and wrapper:

```tsx
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Layout } from "./components/Layout";
import { Dashboard } from "./pages/Dashboard";
import { Runs } from "./pages/Runs";
import { RunDetail } from "./pages/RunDetail";
import { Settings } from "./pages/Settings";
import { Login } from "./pages/Login";
import { useRunsStream } from "./lib/sse";

const queryClient = new QueryClient();

function AppRoutes() {
  useRunsStream();
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route element={<Layout />}>
          <Route path="/" element={<Dashboard />} />
          <Route path="/runs" element={<Runs />} />
          <Route path="/runs/:id" element={<RunDetail />} />
          <Route path="/settings" element={<Settings />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AppRoutes />
    </QueryClientProvider>
  );
}
```

- [ ] **Step 4: Delete `frontend/src/lib/UserContext.tsx`**

```bash
rm frontend/src/lib/UserContext.tsx
```

- [ ] **Step 5: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors — this confirms no remaining file imports the deleted `UserContext.tsx`.

- [ ] **Step 6: Full production build**

Run: `cd frontend && npm run build`
Expected: build succeeds (matches the CI "Frontend typecheck + build" step).

- [ ] **Step 7: Manual end-to-end verification**

As a non-admin user (created via Settings by an admin in Task 13's flow), log in and confirm:
- `/runs` shows no "User" filter dropdown (not admin).
- `/runs` and `/` only ever show that user's own runs, even if other users' runs exist in the DB.

As the admin, confirm:
- `/runs` shows the "User" filter dropdown, populated from `/api/users`, and selecting a value narrows the table.
- `/` and `/runs` show all users' runs by default.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/pages/Runs.tsx frontend/src/pages/Dashboard.tsx frontend/src/App.tsx
git rm frontend/src/lib/UserContext.tsx
git commit -m "feat(AI-7): remove global user switcher, add admin view-as filter on Runs"
```

---

## Post-plan verification

- [ ] Run the full backend suite once more: `uv run pytest backend/ -v` — all tests pass.
- [ ] Run `cd frontend && npx tsc --noEmit && npm run build` — both succeed.
- [ ] Run `cd infra && terraform validate` — succeeds. Do not `terraform apply` without the user's explicit go-ahead (needs a real `session_secret` value in `terraform.tfvars`, and production Cloud Run env change).
- [ ] Manually walk the full bootstrap → login → scoping → account management flow described in Tasks 13-14 against a fresh local DB.
