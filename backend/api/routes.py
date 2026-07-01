import gzip
import json
import asyncio
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query, Header, Request
from sqlmodel import Session, select
from typing import Optional
from sse_starlette.sse import EventSourceResponse
from backend.db import get_session
from backend.models import AgentRun, AgentRunRead, ApiKey, TranscriptStore
from backend import sse as sse_bus
from backend.adapters.claude_code import parse_transcript_content
from backend.watcher import _upsert

PROVIDERS = ("anthropic", "openai", "gemini")
MAX_COMPRESSED_BYTES = 10 * 1024 * 1024   # 10 MB compressed
MAX_INGEST_BYTES = 100 * 1024 * 1024      # 100 MB decompressed

router = APIRouter()


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
    if parent_id is not None:
        query = query.where(AgentRun.parent_id == parent_id)
    elif not include_children:
        # Hide subagent runs by default — they appear in the trace tree of their parent
        query = query.where(AgentRun.parent_id == None)  # noqa: E711
    if ticket:
        runs = [r for r in session.exec(query).all() if ticket in r.ticket_refs]
        runs = runs[offset: offset + limit]
    else:
        runs = session.exec(query.offset(offset).limit(limit)).all()
    return [_to_read(r) for r in runs]


@router.get("/runs/{run_id}", response_model=AgentRunRead)
def get_run(run_id: str, session: Session = Depends(get_session)):
    run = session.get(AgentRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return _to_read(run)


@router.get("/providers")
def list_providers(session: Session = Depends(get_session)):
    runs = session.exec(select(AgentRun)).all()
    return {"providers": list({r.provider for r in runs})}


@router.get("/users")
def list_users(session: Session = Depends(get_session)):
    runs = session.exec(select(AgentRun)).all()
    return {"users": sorted({r.user for r in runs if r.user})}


@router.get("/daily")
def get_daily(
    user: Optional[str] = None,
    days: int = Query(7, ge=1, le=3650),
    session: Session = Depends(get_session),
):
    runs = session.exec(select(AgentRun)).all()
    cutoff = datetime.utcnow() - timedelta(days=days)
    recent = [r for r in runs if r.started_at >= cutoff]
    if user:
        recent = [r for r in recent if r.user == user]
    buckets: dict[str, dict] = {}
    for i in range(days):
        d = (datetime.utcnow() - timedelta(days=days - 1 - i)).strftime("%m/%d")
        buckets[d] = {"date": d, "anthropic": 0, "openai": 0, "gemini": 0,
                      "input_tokens": 0, "output_tokens": 0}
    for r in recent:
        d = r.started_at.strftime("%m/%d")
        if d in buckets:
            buckets[d][r.provider] = buckets[d].get(r.provider, 0) + 1
            buckets[d]["input_tokens"] += r.input_tokens
            buckets[d]["output_tokens"] += r.output_tokens
    return list(buckets.values())


@router.get("/stats")
def get_stats(
    user: Optional[str] = None,
    days: int = Query(7, ge=1, le=3650),
    session: Session = Depends(get_session),
):
    runs = session.exec(select(AgentRun)).all()
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
def list_keys(session: Session = Depends(get_session)):
    keys = session.exec(select(ApiKey).order_by(ApiKey.created_at.desc())).all()
    return [
        {"key_prefix": k.key[:12] + "…", "user": k.user, "created_at": k.created_at}
        for k in keys
    ]


@router.post("/keys", status_code=201)
def create_key(body: dict, session: Session = Depends(get_session)):
    user = (body.get("user") or "").strip()
    if not user:
        raise HTTPException(status_code=422, detail="user is required")
    key = ApiKey(user=user)
    session.add(key)
    session.commit()
    session.refresh(key)
    return {"key": key.key, "user": key.user, "created_at": key.created_at}


@router.delete("/keys/{key_prefix}")
def delete_key(key_prefix: str, session: Session = Depends(get_session)):
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

    run = parse_transcript_content(content, mtime=x_file_mtime)
    if not run:
        raise HTTPException(status_code=422, detail="Could not parse transcript")
    total_tokens = run.input_tokens + run.output_tokens
    if total_tokens < 150:
        return {"id": run.id, "status": "skipped"}
    run.user = api_key.user
    run_id, run_status = run.id, run.status
    _upsert(run)
    await sse_bus.broadcast({"type": "run_updated", "id": run_id})
    return {"id": run_id, "status": run_status}


@router.get("/stream")
async def stream_runs():
    q = sse_bus.subscribe()

    async def generator():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=30)
                    yield {"data": json.dumps(event)}
                except asyncio.TimeoutError:
                    yield {"data": json.dumps({"type": "ping"})}
        except asyncio.CancelledError:
            pass
        finally:
            sse_bus.unsubscribe(q)

    return EventSourceResponse(generator())


def _to_read(run: AgentRun) -> AgentRunRead:
    duration = None
    if run.ended_at and run.started_at:
        duration = (run.ended_at - run.started_at).total_seconds()
    data = run.model_dump()
    return AgentRunRead(**data, duration_seconds=duration)
