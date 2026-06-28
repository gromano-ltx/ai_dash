import json
import asyncio
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query, Header, Request
from sqlmodel import Session, select
from typing import Optional
from sse_starlette.sse import EventSourceResponse
from backend.db import get_session
from backend.models import AgentRun, AgentRunRead, ApiKey
from backend import sse as sse_bus
from backend.adapters.claude_code import parse_transcript_content
from backend.watcher import _upsert

PROVIDERS = ("anthropic", "openai", "gemini")
MAX_INGEST_BYTES = 10 * 1024 * 1024  # 10 MB

router = APIRouter()


@router.get("/runs", response_model=list[AgentRunRead])
def list_runs(
    provider: Optional[str] = None,
    status: Optional[str] = None,
    user: Optional[str] = None,
    ticket: Optional[str] = None,
    limit: int = Query(50, le=500),
    offset: int = 0,
    session: Session = Depends(get_session),
):
    query = select(AgentRun).order_by(AgentRun.started_at.desc())
    if provider:
        query = query.where(AgentRun.provider == provider)
    if status:
        query = query.where(AgentRun.status == status)
    if user:
        query = query.where(AgentRun.user == user)
    if ticket:
        # ticket_refs is JSON; filter in Python before applying limit
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
def get_daily(session: Session = Depends(get_session)):
    runs = session.exec(select(AgentRun)).all()
    cutoff = datetime.utcnow() - timedelta(days=7)
    recent = [r for r in runs if r.started_at >= cutoff]
    days: dict[str, dict] = {}
    for i in range(7):
        d = (datetime.utcnow() - timedelta(days=6 - i)).strftime("%m/%d")
        days[d] = {"date": d, "anthropic": 0, "openai": 0, "gemini": 0,
                   "input_tokens": 0, "output_tokens": 0}
    for r in recent:
        d = r.started_at.strftime("%m/%d")
        if d in days:
            days[d][r.provider] = days[d].get(r.provider, 0) + 1
            days[d]["input_tokens"] += r.input_tokens
            days[d]["output_tokens"] += r.output_tokens
    return list(days.values())


@router.get("/stats")
def get_stats(session: Session = Depends(get_session)):
    runs = session.exec(select(AgentRun)).all()
    cutoff = datetime.utcnow() - timedelta(days=7)
    recent = [r for r in runs if r.started_at >= cutoff]
    return {
        "total_runs_7d": len(recent),
        "total_input_tokens_7d": sum(r.input_tokens for r in recent),
        "total_output_tokens_7d": sum(r.output_tokens for r in recent),
        "total_commits_7d": sum(len(r.git_commits) for r in recent),
        "total_prs_7d": sum(len(r.git_prs) for r in recent),
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


@router.post("/v1/ingest")
async def ingest_transcript(
    request: Request,
    x_api_key: str = Header(...),
    session: Session = Depends(get_session),
):
    api_key = session.get(ApiKey, x_api_key)
    if not api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")
    body = await request.body()
    if len(body) > MAX_INGEST_BYTES:
        raise HTTPException(status_code=413, detail="Payload too large")
    content = body.decode("utf-8", errors="replace")
    if not content.strip():
        raise HTTPException(status_code=400, detail="Empty body")
    run = parse_transcript_content(content)
    if not run:
        raise HTTPException(status_code=422, detail="Could not parse transcript")
    run.user = api_key.user
    _upsert(run)
    await sse_bus.broadcast({"type": "run_updated", "id": run.id})
    return {"id": run.id, "status": run.status}


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
    data = run.model_dump(exclude={"meta"})
    return AgentRunRead(**data, duration_seconds=duration)
