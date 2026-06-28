import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from backend.db import init_db
from backend.api.routes import router
from backend import watcher


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

app.include_router(router, prefix="/api")

_COLLECTOR = Path(__file__).parent.parent / "collector" / "collector.py"

@app.get("/collector.py")
def serve_collector():
    return FileResponse(_COLLECTOR, media_type="text/plain")

@app.get("/install.sh")
def serve_install():
    return FileResponse(Path(__file__).parent.parent / "install.sh", media_type="text/plain")
