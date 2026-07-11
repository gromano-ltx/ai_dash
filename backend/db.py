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
    for column in ("estimated_input_cost_usd", "estimated_output_cost_usd", "estimated_cost_usd"):
        if column not in existing_columns:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE agent_runs ADD COLUMN {column} FLOAT"))
            logger.info(f"[db] added agent_runs.{column} column")

    if "transcript_store" in inspector.get_table_names():
        transcript_columns = {c["name"] for c in inspector.get_columns("transcript_store")}
        if "run_id" not in transcript_columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE transcript_store ADD COLUMN run_id VARCHAR"))
            logger.info("[db] added transcript_store.run_id column")


def find_transcript_store(session: Session, run_id: str):
    """Look up the TranscriptStore row for an AgentRun by its true id.

    Prefers a match on TranscriptStore.run_id — the content-parsed id
    recorded at write time by ingest_transcript (see backend/api/routes.py) —
    which is the only reliable key for providers whose collector-supplied
    X-Session-Id doesn't match the session id embedded in the transcript
    content itself (Codex's "rollout-<timestamp>-<uuid>.jsonl", Gemini main
    sessions' "session-<timestamp>-<shortid>.jsonl"). Falls back to the
    primary-key lookup on session_id for rows written before this fix
    existed, where session_id and run_id happen to coincide (Claude Code,
    Gemini subagents).
    """
    from backend.models import TranscriptStore

    stored = session.exec(
        select(TranscriptStore).where(TranscriptStore.run_id == run_id)
    ).first()
    if stored:
        return stored
    return session.get(TranscriptStore, run_id)


def _backfill_cached_input_tokens(session: Session):
    """One-time backfill: recompute input_tokens/meta.cached_input_tokens for
    rows ingested before AI-54's fix, by re-parsing their stored transcript
    content with the corrected adapter logic. Only these two fields are
    touched — status/started_at/ended_at/label etc. on the existing row are
    left exactly as they are; a fresh re-parse can't reliably reconstruct
    those (e.g. it has no access to the original file's mtime), but
    input_tokens/meta are fully and correctly derivable from the stored
    transcript content alone. Gemini rows are skipped — no real ones existed
    at the time of this fix, so nothing to backfill there.
    """
    from backend.adapters import claude_code, codex
    from backend.models import AgentRun

    parsers = {"openai": codex.parse_transcript_content, "anthropic": claude_code.parse_transcript_content}
    rows = session.exec(
        select(AgentRun).where(AgentRun.provider.in_(list(parsers.keys())))
    ).all()
    migrated = 0
    for run in rows:
        # cache_creation_input_tokens was added after cached_input_tokens and
        # only anthropic transcripts carry it (Codex's caching has no
        # separate write-side token count), so a previously-migrated
        # anthropic row must still be re-parsed once to pick up the new key;
        # openai rows are unaffected and stay migrated on the older key alone.
        already_migrated = isinstance(run.meta, dict) and "cached_input_tokens" in run.meta and (
            run.provider != "anthropic" or "cache_creation_input_tokens" in run.meta
        )
        if already_migrated:
            continue
        stored = find_transcript_store(session, run.id)
        if not stored:
            continue
        parse_fn = parsers[run.provider]
        reparsed = parse_fn(stored.content)
        if not reparsed:
            continue
        run.input_tokens = reparsed.input_tokens
        run.meta = reparsed.meta
        session.add(run)
        migrated += 1
    if migrated:
        session.commit()
        print(f"[db] backfilled cached_input_tokens for {migrated} runs")


def _backfill_ticket_refs(session: Session):
    """One-time-per-row backfill: recompute ticket_refs for rows ingested
    before the fix that excludes PR self-references from _extract_tickets
    (squash-merge commit messages read "<title> (#40)", which used to be
    recorded as a bogus ticket ref even though the same PR is already listed,
    as a full URL, in git_prs). Only ticket_refs is touched. Naturally
    idempotent — skips rows where a fresh parse yields the same value, so
    no separate "already migrated" marker is needed. Gemini rows are
    skipped, matching AI-54's cached_input_tokens backfill.
    """
    from backend.adapters import claude_code, codex
    from backend.models import AgentRun

    parsers = {"openai": codex.parse_transcript_content, "anthropic": claude_code.parse_transcript_content}
    rows = session.exec(
        select(AgentRun).where(AgentRun.provider.in_(list(parsers.keys())))
    ).all()
    migrated = 0
    for run in rows:
        stored = find_transcript_store(session, run.id)
        if not stored:
            continue
        parse_fn = parsers[run.provider]
        reparsed = parse_fn(stored.content)
        if not reparsed or reparsed.ticket_refs == run.ticket_refs:
            continue
        run.ticket_refs = reparsed.ticket_refs
        session.add(run)
        migrated += 1
    if migrated:
        session.commit()
        print(f"[db] backfilled ticket_refs for {migrated} runs")


def get_session():
    with Session(engine) as session:
        yield session


def _seed():
    from sqlalchemy import text
    from backend.models import AgentRun, ApiKey
    from backend.watcher import MIN_TOKENS_TO_PERSIST
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
                f"(input_tokens + output_tokens < {MIN_TOKENS_TO_PERSIST}) OR "
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
            # Backfill ended_at for runs already stuck "done" with a null
            # duration — the stale-run cleanup only fixes rows it *currently*
            # marks done (status == "running"); a row the old, pre-fix sweep
            # already flipped to "done" is skipped forever otherwise, since
            # it's no longer "running" by the time the fixed sweep looks.
            #
            # Only backfill when updated_at is actually known: falling back
            # to started_at would make ended_at == started_at, fabricating a
            # false 0s duration for a row we simply have no real data for —
            # leaving ended_at null (still "unknown") is more honest than that.
            stuck = session.exec(
                select(AgentRun).where(
                    AgentRun.status == "done",
                    AgentRun.ended_at == None,  # noqa: E711
                    AgentRun.updated_at != None,  # noqa: E711
                )
            ).all()
            if stuck:
                for r in stuck:
                    r.ended_at = r.updated_at
                    session.add(r)
                session.commit()
                print(f"[db] backfilled ended_at for {len(stuck)} runs stuck done with null duration")
            _backfill_cached_input_tokens(session)
            _backfill_ticket_refs(session)
            # Backfill: existing rows already have model/input_tokens/
            # output_tokens stored, so cost can be computed retroactively,
            # unlike the ended_at backfill above where the source data for old
            # rows didn't exist. Recomputes for every row (not just
            # estimated_cost_usd IS NULL) and only writes when the value
            # actually changes, so it's naturally idempotent, self-heals rows
            # whose model tier gets added to PRICING after they were ingested,
            # AND self-corrects rows costed under a since-fixed pricing bug
            # (e.g. the cache-read tokens being priced at $0 before this fix).
            # Runs *after* _backfill_cached_input_tokens so cost is computed
            # from the corrected (cache-excluded) input_tokens, not the stale
            # pre-AI-54 value.
            from backend.pricing import estimate_cost

            all_runs = session.exec(select(AgentRun)).all()
            recosted = 0
            for r in all_runs:
                run_meta = r.meta if isinstance(r.meta, dict) else {}
                cached = run_meta.get("cached_input_tokens", 0)
                cache_creation = run_meta.get("cache_creation_input_tokens", 0)
                cost = estimate_cost(r.provider, r.model, r.input_tokens, r.output_tokens, cached, cache_creation)
                new_total = cost.total_usd if cost else None
                if new_total == r.estimated_cost_usd:
                    continue
                r.estimated_input_cost_usd = cost.input_usd if cost else None
                r.estimated_output_cost_usd = cost.output_usd if cost else None
                r.estimated_cost_usd = new_total
                session.add(r)
                recosted += 1
            if recosted:
                session.commit()
                print(f"[db] backfilled estimated cost for {recosted} runs")
