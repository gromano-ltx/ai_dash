import os
from datetime import datetime, timedelta
from sqlmodel import SQLModel, create_engine, Session, select

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./ai_dash.db")
engine = create_engine(DATABASE_URL)


def init_db():
    SQLModel.metadata.create_all(engine)
    _seed()


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
            # Remove subagent stub sessions: zero-token noise and system-prompt-labeled sub-agents
            deleted = session.exec(text(
                "DELETE FROM agent_runs WHERE "
                "(input_tokens + output_tokens < 10) OR "
                "(label LIKE 'You are %')"
            ))
            session.commit()
            if deleted.rowcount:
                print(f"[db] cleaned up {deleted.rowcount} subagent/stub sessions")
            # Clean up git_commits/git_prs that stored bash commands instead of hashes/URLs
            try:
                session.exec(text(
                    "UPDATE agent_runs SET git_commits = '[]'::json "
                    "WHERE git_commits::text != '[]' "
                    "AND git_commits::text LIKE '%\"git %'"
                ))
                session.exec(text(
                    "UPDATE agent_runs SET git_prs = '[]'::json "
                    "WHERE git_prs::text != '[]' "
                    "AND git_prs::text NOT LIKE '%https://%'"
                ))
                # Clear github_repo from meta where it's the placeholder seed URL
                session.exec(text(
                    "UPDATE agent_runs SET meta = meta - 'github_repo' "
                    "WHERE meta->>'github_repo' = 'https://github.com/org/repo'"
                ))
                session.commit()
            except Exception:
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
