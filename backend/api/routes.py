import gzip
import json
import asyncio
import logging
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query, Header, Request
from sqlmodel import Session, select
from typing import Optional
from sse_starlette.sse import EventSourceResponse
from backend.db import get_session
from backend.models import AgentRun, AgentRunRead, ApiKey, TranscriptStore, User
from backend.auth import get_optional_user, require_admin
from backend import sse as sse_bus
from backend.adapters import claude_code, codex, gemini_cli
from backend.watcher import _upsert

PROVIDERS = ("anthropic", "openai", "gemini")
PROVIDER_ADAPTERS = {
    "anthropic": claude_code.parse_transcript_content,
    "openai": codex.parse_transcript_content,
    "gemini": gemini_cli.parse_transcript_content,
}


def _select_parser(provider: str):
    parse_fn = PROVIDER_ADAPTERS.get(provider)
    if parse_fn is None:
        raise HTTPException(status_code=422, detail=f"Unknown provider: {provider!r}")
    return parse_fn


MAX_COMPRESSED_BYTES = 10 * 1024 * 1024   # 10 MB compressed
MAX_INGEST_BYTES = 100 * 1024 * 1024      # 100 MB decompressed
MAX_DELETE_BATCH = 100                    # hard cap on ids per DELETE /runs call

logger = logging.getLogger(__name__)

router = APIRouter()


def _visible_runs_query():
    """Base query for runs that should count toward dashboard aggregates.

    Excludes zero-token stub rows and subagent rows (parent_id set), which
    only appear nested in their parent's trace tree — matching the default
    (non include_children) filtering that list_runs applies.
    """
    return select(AgentRun).where(
        (AgentRun.input_tokens + AgentRun.output_tokens) > 0,
        AgentRun.parent_id == None,  # noqa: E711
    )


def _visible_runs(session: Session, user: Optional[User] = None) -> list[AgentRun]:
    runs = session.exec(_visible_runs_query()).all()
    if user and not user.is_admin:
        runs = [r for r in runs if r.user == user.username]
    return runs


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
        # Hide subagent runs by default — they appear in the trace tree of their parent
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


@router.delete("/runs")
def delete_runs(
    body: dict,
    current: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    ids = body.get("ids")
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        raise HTTPException(status_code=422, detail="ids must be a list of strings")
    if len(ids) > MAX_DELETE_BATCH:
        raise HTTPException(
            status_code=422,
            detail=f"Cannot delete more than {MAX_DELETE_BATCH} runs per request",
        )
    dry_run = bool(body.get("dry_run", False))

    deleted: list[str] = []
    not_found: list[str] = []
    processed: set[str] = set()
    # Captured before any deletion so the audit log still means something
    # once the rows are gone.
    audit_entries: list[dict] = []

    for run_id in ids:
        if run_id in processed:
            continue

        run = session.get(AgentRun, run_id)
        if not run:
            not_found.append(run_id)
            processed.add(run_id)
            continue

        children = session.exec(select(AgentRun).where(AgentRun.parent_id == run_id)).all()
        for child in children:
            if child.id in processed:
                continue
            audit_entries.append(_describe_run(child))
            if not dry_run:
                _delete_run_and_transcript(session, child)
            deleted.append(child.id)
            processed.add(child.id)

        audit_entries.append(_describe_run(run))
        if not dry_run:
            _delete_run_and_transcript(session, run)
        deleted.append(run.id)
        processed.add(run.id)

    if dry_run:
        return {"deleted": deleted, "not_found": not_found}

    session.commit()
    summary = ", ".join(
        f"{e['id']} (provider={e['provider']}, user={e['user']}, started_at={e['started_at']})"
        for e in audit_entries
    )
    logger.info(f"[admin] {current.username} deleted runs: {summary}")
    return {"deleted": deleted, "not_found": not_found}


def _describe_run(run: AgentRun) -> dict:
    """Capture identifying fields before deletion for audit logging."""
    return {
        "id": run.id,
        "provider": run.provider,
        "user": run.user,
        "started_at": run.started_at.isoformat() if run.started_at else None,
    }


def _delete_run_and_transcript(session: Session, run: AgentRun) -> None:
    stored = session.get(TranscriptStore, run.id)
    if stored:
        session.delete(stored)
    session.delete(run)


@router.get("/providers")
def list_providers(
    current_user: Optional[User] = Depends(get_optional_user),
    session: Session = Depends(get_session),
):
    runs = _visible_runs(session, current_user)
    return {"providers": list({r.provider for r in runs})}


@router.get("/users")
def list_users(
    current_user: Optional[User] = Depends(get_optional_user),
    session: Session = Depends(get_session),
):
    runs = _visible_runs(session, current_user)
    return {"users": sorted({r.user for r in runs if r.user})}


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
    # Bucket by full ISO date internally so dates a year+ apart that share
    # the same month/day never collide; format for display (MM/DD, what the
    # frontend expects) only after grouping.
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


@router.post("/v1/ingest")
async def ingest_transcript(
    request: Request,
    x_api_key: str = Header(...),
    x_session_id: Optional[str] = Header(None),
    x_file_offset: int = Header(0),
    x_file_mtime: Optional[float] = Header(None),
    x_provider: str = Header("anthropic"),
    x_parent_id: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    api_key = session.get(ApiKey, x_api_key)
    if not api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")
    body = await request.body()
    if len(body) > MAX_COMPRESSED_BYTES:
        raise HTTPException(status_code=413, detail="Payload too large")

    if request.headers.get("content-encoding", "").lower() == "gzip":
        try:
            body = gzip.decompress(body)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid gzip content")
        if len(body) > MAX_INGEST_BYTES:
            raise HTTPException(status_code=413, detail="Decompressed payload too large")

    new_content = body.decode("utf-8", errors="replace")

    if x_session_id and x_file_offset > 0:
        stored = session.get(TranscriptStore, x_session_id)
        if not stored:
            raise HTTPException(status_code=400, detail="Session state not found. Resend from offset 0.")
        content = stored.content + new_content
    else:
        content = new_content

    if not content.strip():
        raise HTTPException(status_code=400, detail="Empty body")

    if x_session_id:
        stored = session.get(TranscriptStore, x_session_id)
        if stored:
            stored.content = content
        else:
            stored = TranscriptStore(session_id=x_session_id, content=content)
        session.add(stored)
        session.commit()

    parse_fn = _select_parser(x_provider)
    run = parse_fn(content, mtime=x_file_mtime, parent_id=x_parent_id)
    if not run:
        raise HTTPException(status_code=422, detail="Could not parse transcript")
    total_tokens = run.input_tokens + run.output_tokens
    if total_tokens < 150:
        return {"id": run.id, "status": "skipped"}
    run.user = api_key.user
    run_id, run_status = run.id, run.status
    _upsert(run)
    await sse_bus.broadcast({"type": "run_updated", "id": run_id, "user": run.user})
    return {"id": run_id, "status": run_status}


@router.get("/stream")
async def stream_runs(
    current_user: Optional[User] = Depends(get_optional_user),
):
    q = sse_bus.subscribe()

    async def generator():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=30)
                    if (
                        current_user
                        and not current_user.is_admin
                        and event.get("type") == "run_updated"
                        and event.get("user") != current_user.username
                    ):
                        continue
                    yield {"data": json.dumps(event)}
                except asyncio.TimeoutError:
                    yield {"data": json.dumps({"type": "ping"})}
        except asyncio.CancelledError:
            pass
        finally:
            sse_bus.unsubscribe(q)

    return EventSourceResponse(generator())


def _parents_with_running_children(session: Session, run_ids: list[str]) -> set[str]:
    if not run_ids:
        return set()
    parent_ids = session.exec(
        select(AgentRun.parent_id).where(
            AgentRun.parent_id.in_(run_ids), AgentRun.status == "running"
        )
    ).all()
    return set(parent_ids)


def _to_read(run: AgentRun, running_parents: set[str] = frozenset()) -> AgentRunRead:
    duration = None
    if run.ended_at and run.started_at:
        duration = (run.ended_at - run.started_at).total_seconds()
    data = run.model_dump()
    if data["status"] == "done" and run.id in running_parents:
        # A parent's own transcript can go idle while it waits on Task-tool
        # subagents, tripping the done-timeout even though children are
        # still actively running — reflect the children's activity instead.
        data["status"] = "running"
    return AgentRunRead(**data, duration_seconds=duration)
