import asyncio
import base64
import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from backend.db import init_db
from backend.api.routes import router
from backend import watcher

_ROOT = Path(__file__).parent.parent
_FRONTEND = _ROOT / "frontend" / "dist"
_DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(watcher.watch())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="AI Dash", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5174"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    # ingest has its own API key auth; skip basic auth for it
    if not _DASHBOARD_PASSWORD or request.url.path.startswith("/api/v1/ingest"):
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


@app.get("/collector.py")
def serve_collector():
    return FileResponse(_ROOT / "collector" / "collector.py", media_type="text/plain")


@app.get("/install.sh")
def serve_install():
    return FileResponse(_ROOT / "install.sh", media_type="text/plain")


# Serve built frontend — only present in production Docker image
if _FRONTEND.exists():
    app.mount("/", StaticFiles(directory=_FRONTEND, html=True), name="frontend")
