import asyncio
from pathlib import Path
from sqlmodel import Session
from backend.db import engine
from backend.models import AgentRun
from backend.adapters.claude_code import parse_transcript, scan_all_transcripts
from backend import sse
from backend.pricing import estimate_cost

# Runs below this combined token count are treated as trivial/stub sessions
# and are not persisted. Mirrors the threshold enforced in
# backend/api/routes.py's ingest_transcript endpoint.
MIN_TOKENS_TO_PERSIST = 150


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
                if run and _upsert(run):
                    await sse.broadcast({"type": "run_updated", "id": run.id, "user": run.user})
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        print(f"[watcher] error: {exc}")


def _upsert(run: AgentRun) -> bool:
    """Insert or update `run`. Returns False (no-op) for trivial/stub runs
    below MIN_TOKENS_TO_PERSIST, matching the ingest endpoint's behavior."""
    if run.input_tokens + run.output_tokens < MIN_TOKENS_TO_PERSIST:
        return False
    meta = run.meta if isinstance(run.meta, dict) else {}
    cached_input_tokens = meta.get("cached_input_tokens", 0)
    cache_creation_input_tokens = meta.get("cache_creation_input_tokens", 0)
    cost = estimate_cost(
        run.provider, run.model, run.input_tokens, run.output_tokens,
        cached_input_tokens, cache_creation_input_tokens,
    )
    if cost:
        run.estimated_input_cost_usd = cost.input_usd
        run.estimated_output_cost_usd = cost.output_usd
        run.estimated_cost_usd = cost.total_usd
    with Session(engine) as session:
        existing = session.get(AgentRun, run.id)
        if existing:
            # Don't let `user` flip depending on write order between the
            # local watcher and the remote collector's ingest path: only
            # adopt the incoming user if the existing run doesn't have one.
            for key, val in run.model_dump(exclude={"id", "user"}).items():
                setattr(existing, key, val)
            if run.user and not existing.user:
                existing.user = run.user
            session.add(existing)
        else:
            session.add(run)
        session.commit()
    return True
