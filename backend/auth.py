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
