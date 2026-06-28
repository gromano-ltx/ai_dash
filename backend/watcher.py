import asyncio
from pathlib import Path
from sqlmodel import Session
from backend.db import engine
from backend.models import AgentRun
from backend.adapters.claude_code import parse_transcript, scan_all_transcripts
from backend import sse


async def watch() -> None:
    for run in scan_all_transcripts():
        _upsert(run)

    base = Path.home() / ".claude" / "projects"
    if not base.exists():
        return

    try:
        from watchfiles import awatch
        async for changes in awatch(str(base)):
            changed = {Path(path) for _, path in changes if path.endswith(".jsonl")}
            for path in changed:
                run = parse_transcript(path)
                if run:
                    _upsert(run)
                    await sse.broadcast({"type": "run_updated", "id": run.id})
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        print(f"[watcher] error: {exc}")


def _upsert(run: AgentRun) -> None:
    with Session(engine) as session:
        existing = session.get(AgentRun, run.id)
        if existing:
            for key, val in run.model_dump(exclude={"id"}).items():
                setattr(existing, key, val)
            session.add(existing)
        else:
            session.add(run)
        session.commit()
