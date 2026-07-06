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

# Modern Chrome/Firefox/Safari treat "localhost" as a secure context, so a
# Secure cookie still round-trips in local dev over plain http://localhost
# even though this is unconditionally True in production. Hardcoding this
# avoids relying on X-Forwarded-Proto detection, which Cloud Run's uvicorn
# process isn't currently configured to trust (no --proxy-headers flag).
#
# Tests monkeypatch this to False: FastAPI's TestClient talks to the app
# over a plain http://testserver base URL, and httpx (correctly) won't
# resend a Secure cookie on a later request within the same client, which
# would otherwise break every test that logs in and then makes a second,
# cookie-authenticated request.
_COOKIE_SECURE = True


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
        secure=_COOKIE_SECURE,
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
