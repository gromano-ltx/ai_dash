import logging
import os
from dotenv import load_dotenv
from sqlmodel import SQLModel, create_engine, Session, select

# Picks up DATABASE_URL from a local .env file (see .env.example) so local
# dev can point at the docker-compose Postgres instead of the SQLite
# fallback below, matching production (Cloud SQL/Postgres) more closely.
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./ai_dash.db")
engine = create_engine(DATABASE_URL)

logger = logging.getLogger(__name__)

# (user, label) pairs of the fake demo runs _seed() used to insert on first
# deploy, before real data existed. Kept here so the one-time cleanup below
# can identify and remove them precisely.
_SEED_DEMO_ROWS = frozenset({
    ("gabby", "Fix authentication bug in login flow"),
    ("marco", "Add dark mode support to dashboard"),
    ("gabby", "Refactor payment service to use new Stripe API"),
    ("alex", "Generate API documentation from route definitions"),
    ("marco", "Write unit tests for user service"),
    ("gabby", "Optimize database queries in reporting module"),
    ("alex", "Migrate legacy config files to new format"),
})


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
            # One-time cleanup: delete the fake runs _seed() used to insert on
            # first deploy (before real data existed). Matched on the exact
            # (user, label) pairs those rows were created with, so this can
            # never touch real data — a real run would have to coincidentally
            # share both a fake demo username and its exact synthetic label.
            to_delete = [
                r for r in session.exec(select(AgentRun)).all()
                if (r.user, r.label) in _SEED_DEMO_ROWS
            ]
            if to_delete:
                for r in to_delete:
                    session.delete(r)
                session.commit()
                print(f"[db] removed {len(to_delete)} seed/demo rows")
