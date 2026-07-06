import asyncio
import base64
import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from backend.db import init_db
from backend.api.routes import router
from backend.api.auth_routes import router as auth_router
from backend import watcher

_ROOT = Path(__file__).parent.parent
_FRONTEND = _ROOT / "frontend" / "dist"
_DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")


async def _cleanup_stale_runs():
    """Mark 'running' sessions as 'done' if they haven't been updated in 10 minutes.

    Sessions are stored as 'running' when first ingested while fresh. The collector
    doesn't re-ship unchanged files, so without this task they stay 'running' forever.

    Keyed on updated_at (last time this row was actually re-parsed/upserted),
    not started_at — a session active for hours (started_at long past the
    cutoff) would otherwise get marked stale/'done' after just 10 minutes
    even while still genuinely running. updated_at can be NULL for rows
    created before this column existed, so those are treated as stale too.
    """
    from datetime import datetime, timedelta
    from sqlmodel import select, or_
    from backend.db import get_session as _get_session
    from backend.models import AgentRun
    while True:
        await asyncio.sleep(120)
        try:
            with next(_get_session()) as session:
                cutoff = datetime.utcnow() - timedelta(minutes=10)
                stale = session.exec(
                    select(AgentRun).where(
                        AgentRun.status == "running",
                        or_(AgentRun.updated_at < cutoff, AgentRun.updated_at == None),  # noqa: E711
                    )
                ).all()
                for run in stale:
                    run.status = "done"
                    # Only set ended_at when updated_at gives a real proxy
                    # for when the session went idle. Falling back to
                    # started_at would make ended_at == started_at — a
                    # fabricated 0s duration, which is more misleading than
                    # leaving duration unknown (still "—") for these rows.
                    if run.updated_at:
                        run.ended_at = run.updated_at
                    session.add(run)
                if stale:
                    session.commit()
                    print(f"[cleanup] marked {len(stale)} stale runs as done")
        except Exception as exc:
            print(f"[cleanup] error: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    watcher_task = asyncio.create_task(watcher.watch())
    cleanup_task = asyncio.create_task(_cleanup_stale_runs())
    yield
    watcher_task.cancel()
    cleanup_task.cancel()
    for t in (watcher_task, cleanup_task):
        try:
            await t
        except asyncio.CancelledError:
            pass


app = FastAPI(title="AI Dash", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5174"],
    allow_methods=["*"],
    allow_headers=["*"],
)


_PUBLIC_PATHS = frozenset({"/install.sh", "/collector.py"})


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    # ingest has its own API key auth; the installer + collector download
    # routes must be fetchable by a brand-new user who doesn't have the
    # dashboard password yet — skip basic auth for all three.
    if (
        not _DASHBOARD_PASSWORD
        or request.url.path.startswith("/api/v1/ingest")
        or request.url.path in _PUBLIC_PATHS
    ):
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


app.include_router(router, prefix="/api")
app.include_router(auth_router, prefix="/api")


@app.get("/collector.py")
def serve_collector():
    return FileResponse(_ROOT / "collector" / "collector.py", media_type="text/plain")


@app.get("/install.sh")
def serve_install():
    return FileResponse(_ROOT / "install.sh", media_type="text/plain")


# Serve built frontend — only present in production Docker image.
# StaticFiles alone can't handle SPA routing (returns 404 for /runs, /runs/:id etc.
# because those paths don't exist on disk). The catch-all below serves actual asset
# files directly and falls back to index.html for everything else.
if _FRONTEND.exists():
    _FRONTEND_RESOLVED = _FRONTEND.resolve()

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        target = (_FRONTEND / full_path).resolve()
        # Prevent path traversal (proper containment check, not a string prefix
        # comparison, which would wrongly allow siblings like "dist-backup").
        if not target.is_relative_to(_FRONTEND_RESOLVED):
            return Response(status_code=403)
        if target.is_file():
            return FileResponse(target)
        return FileResponse(_FRONTEND / "index.html")
