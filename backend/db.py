import logging
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
from sqlmodel import SQLModel, create_engine, Session, select

# Picks up DATABASE_URL from a local .env file (see .env.example) so local
# dev can point at the docker-compose Postgres instead of the SQLite
# fallback below, matching production (Cloud SQL/Postgres) more closely.
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./ai_dash.db")
engine = create_engine(DATABASE_URL)

logger = logging.getLogger(__name__)


def init_db():
    _add_missing_columns()
    SQLModel.metadata.create_all(engine)
    _seed()


def _add_missing_columns():
    """create_all() only creates missing tables, never ALTERs existing ones —
    so a new column added to a model (e.g. AgentRun.updated_at) has to be
    backfilled by hand for a table that already exists in production. The
    ADD COLUMN syntax below is plain SQL, valid on both SQLite and Postgres."""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    if "agent_runs" not in inspector.get_table_names():
        return
    existing_columns = {c["name"] for c in inspector.get_columns("agent_runs")}
    if "updated_at" not in existing_columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE agent_runs ADD COLUMN updated_at TIMESTAMP"))
        logger.info("[db] added agent_runs.updated_at column")


def get_session():
    with Session(engine) as session:
        yield session


def _seed():
    from sqlalchemy import text
    from backend.models import AgentRun, ApiKey
    with Session(engine) as session:
        if not session.exec(select(ApiKey)).first():
            key = ApiKey(key="adk_devkey_local", user="Gabby")
            session.add(key)
            session.commit()
            print(f"[db] dev API key: adk_devkey_local")
        else:
            # Fix user="local" → "Gabby" from initial deploy
            rows = session.exec(text("UPDATE api_keys SET \"user\"='Gabby' WHERE \"user\"='local'"))
            runs = session.exec(text("UPDATE agent_runs SET \"user\"='Gabby' WHERE \"user\"='local'"))
            session.commit()
            if runs.rowcount:
                print(f"[db] migrated {runs.rowcount} runs: user='local' → 'Gabby'")
            # Remove zero-token stubs, system-prompt-labeled sub-agents, and trivial micro-sessions
            deleted = session.exec(text(
                "DELETE FROM agent_runs WHERE "
                "(input_tokens + output_tokens < 150) OR "
                "(label LIKE 'You are %')"
            ))
            session.commit()
            if deleted.rowcount:
                print(f"[db] cleaned up {deleted.rowcount} subagent/stub sessions")
            # Clean up git_commits/git_prs that stored bash commands instead of hashes/URLs.
            # Done in Python (rather than dialect-specific SQL like Postgres's ::json casts
            # and jsonb `-` operator) so it works on both SQLite and Postgres — the previous
            # Postgres-only SQL raised on the default SQLite engine and was silently rolled
            # back by a bare except, so this cleanup never actually ran.
            try:
                for run in session.exec(select(AgentRun)).all():
                    dirty = False
                    if run.git_commits and any(
                        isinstance(c, str) and c.startswith("git ") for c in run.git_commits
                    ):
                        run.git_commits = []
                        dirty = True
                    if run.git_prs and not any(
                        isinstance(p, str) and "https://" in p for p in run.git_prs
                    ):
                        run.git_prs = []
                        dirty = True
                    # Clear github_repo from meta where it's the placeholder seed URL
                    if isinstance(run.meta, dict) and run.meta.get("github_repo") == "https://github.com/org/repo":
                        run.meta = {k: v for k, v in run.meta.items() if k != "github_repo"}
                        dirty = True
                    # Null out single-word task descriptions (e.g. "pwd", "ls") — not meaningful
                    if run.task_description is not None and " " not in run.task_description:
                        run.task_description = None
                        dirty = True
                    if dirty:
                        session.add(run)
                session.commit()
            except Exception:
                logger.exception("[db] failed to clean up malformed agent_runs data")
                session.rollback()
        if session.exec(select(AgentRun)).first():
            return
        now = datetime.utcnow()
        runs = [
            AgentRun(
                provider="anthropic", model="claude-sonnet-4-6", status="done",
                label="Fix authentication bug in login flow",
                task_description="Fix the JWT token expiry issue causing users to be logged out prematurely",
                user="gabby",
                git_commits=["a1b2c3d", "e4f5g6h"],
                git_prs=["https://github.com/org/repo/pull/142"],
                ticket_refs=["LINEAR-234"],
                input_tokens=45230, output_tokens=8920,
                started_at=now - timedelta(hours=2),
                ended_at=now - timedelta(hours=1, minutes=45),
            ),
            AgentRun(
                provider="openai", model="gpt-4o", status="done",
                label="Add dark mode support to dashboard",
                task_description="Implement dark mode toggle with system preference detection",
                user="marco",
                git_commits=["i7j8k9l"],
                git_prs=[],
                ticket_refs=["LINEAR-198"],
                input_tokens=32100, output_tokens=6500,
                started_at=now - timedelta(hours=4),
                ended_at=now - timedelta(hours=3, minutes=40),
            ),
            AgentRun(
                provider="anthropic", model="claude-opus-4-8", status="running",
                label="Refactor payment service to use new Stripe API",
                task_description="Migrate from Stripe v2 to v3 API, update webhook handlers",
                user="gabby",
                ticket_refs=["LINEAR-267"],
                input_tokens=12400, output_tokens=3100,
                started_at=now - timedelta(minutes=20),
            ),
            AgentRun(
                provider="gemini", model="gemini-2.0-flash", status="failed",
                label="Generate API documentation from route definitions",
                task_description="Auto-generate OpenAPI docs from existing FastAPI route definitions",
                user="alex",
                ticket_refs=["JIRA-89"],
                input_tokens=18900, output_tokens=2200,
                started_at=now - timedelta(hours=1),
                ended_at=now - timedelta(minutes=50),
            ),
            AgentRun(
                provider="openai", model="gpt-4o-mini", status="done",
                label="Write unit tests for user service",
                task_description="Add comprehensive unit tests for the user authentication service",
                user="marco",
                git_commits=["m1n2o3p", "q4r5s6t", "u7v8w9x"],
                git_prs=["https://github.com/org/repo/pull/138"],
                ticket_refs=["LINEAR-201"],
                input_tokens=28700, output_tokens=9300,
                started_at=now - timedelta(hours=6),
                ended_at=now - timedelta(hours=5, minutes=30),
            ),
            AgentRun(
                provider="anthropic", model="claude-sonnet-4-6", status="done",
                label="Optimize database queries in reporting module",
                task_description="Add indexes and rewrite N+1 queries in the reports endpoint",
                user="gabby",
                git_commits=["y1z2a3b"],
                git_prs=["https://github.com/org/repo/pull/135"],
                ticket_refs=["LINEAR-189"],
                input_tokens=22100, output_tokens=5400,
                started_at=now - timedelta(hours=8),
                ended_at=now - timedelta(hours=7, minutes=45),
            ),
            AgentRun(
                provider="gemini", model="gemini-2.0-pro", status="done",
                label="Migrate legacy config files to new format",
                user="alex",
                git_commits=["c4d5e6f", "g7h8i9j"],
                ticket_refs=["LINEAR-212"],
                input_tokens=15600, output_tokens=4100,
                started_at=now - timedelta(days=1),
                ended_at=now - timedelta(hours=23),
            ),
        ]
        for run in runs:
            session.add(run)
        session.commit()
