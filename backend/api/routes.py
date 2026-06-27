import json
import asyncio
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select
from typing import Optional, AsyncGenerator
from sse_starlette.sse import EventSourceResponse
from backend.db import get_session
from backend.models import AgentRun, AgentRunRead
from backend import sse as sse_bus

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
    runs = session.exec(query.offset(offset).limit(limit)).all()
    if ticket:
        runs = [r for r in runs if ticket in r.ticket_refs]
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


@router.get("/stats")
def get_stats(session: Session = Depends(get_session)):
    runs = session.exec(select(AgentRun)).all()
    from datetime import datetime, timedelta
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
    }


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
