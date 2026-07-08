# AI-5: Cost Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compute and display an estimated USD cost per run (and a total for the dashboard's selected time range), based on token counts and a hardcoded per-model pricing table.

**Architecture:** A new pure `backend/pricing.py` module maps `(provider, model)` to a price tier via substring matching and computes cost from token counts. This is called once inside `backend/watcher.py`'s `_upsert()` (the single choke point both ingest paths share) and stored on three new `AgentRun` columns. A one-time startup backfill (matching the existing `_seed()` pattern in `backend/db.py`) computes cost retroactively for historical rows. `GET /api/stats` sums the new field into a `total_cost_usd` key; the frontend adds one dashboard stat card and one run-detail breakdown card.

**Tech Stack:** FastAPI + SQLModel (existing), no new dependencies.

## Global Constraints

- Pricing covers all three providers (Anthropic, OpenAI, Gemini) — extended beyond the ticket's literal Anthropic-only wording since OpenAI/Gemini adapters have since shipped.
- Model-to-price matching is case-insensitive substring matching on a tier keyword (e.g. `"sonnet"` matches any string containing it), not exact string matching. First match in the tier list wins.
- A model that doesn't match any known tier gets `None` for all three cost fields — never a fallback/guessed price. Such runs are excluded from `total_cost_usd`.
- Cost is recomputed on every `_upsert()` call (insert AND update), not just first insert.
- The pricing table is a hardcoded Python constant (no config file, no DB-backed/admin-editable pricing) — matches the DoD's literal wording and this codebase's existing style (no Alembic, hand-rolled migrations).
- Pricing figures are best-effort placeholders — the `backend/pricing.py` module must carry a `NEEDS VERIFICATION` comment directly in the source, not just in this plan.
- Historical rows get a one-time backfill (following the existing `_seed()` pattern in `backend/db.py`), which only touches rows where `estimated_cost_usd IS NULL` — self-healing for newly-added tiers, but does NOT retroactively fix a row whose price was already computed under an since-corrected price.
- `GET /api/daily` is explicitly untouched (no daily cost chart) — out of scope per the DoD.
- Frontend has no test runner in this repo (established pre-existing constraint) — frontend verification is manual.
- Backend tests run with `uv run pytest <path> -v`.

---

### Task 1: Pricing module

**Files:**
- Create: `backend/pricing.py`
- Test: `backend/test_pricing.py`

**Interfaces:**
- Produces:
  - `backend.pricing.ModelPrice` — `NamedTuple(input_per_1m_usd: float, output_per_1m_usd: float)`
  - `backend.pricing.EstimatedCost` — `NamedTuple(input_usd: float, output_usd: float, total_usd: float)`
  - `backend.pricing.PRICING: dict[str, list[tuple[str, ModelPrice]]]`
  - `backend.pricing.estimate_cost(provider: str, model: str, input_tokens: int, output_tokens: int) -> Optional[EstimatedCost]`

- [ ] **Step 1: Write the failing tests**

```python
from backend.pricing import estimate_cost


def test_matches_anthropic_sonnet_tier():
    result = estimate_cost("anthropic", "claude-sonnet-4-5-20250929", 1_000_000, 1_000_000)
    assert result is not None
    assert result.input_usd == 3.00
    assert result.output_usd == 15.00
    assert result.total_usd == 18.00


def test_matches_anthropic_opus_tier():
    result = estimate_cost("anthropic", "claude-opus-4-20250514", 1_000_000, 1_000_000)
    assert result is not None
    assert result.input_usd == 15.00
    assert result.output_usd == 75.00


def test_matches_anthropic_haiku_tier():
    result = estimate_cost("anthropic", "claude-haiku-4-5-20251001", 1_000_000, 1_000_000)
    assert result is not None
    assert result.input_usd == 0.80
    assert result.output_usd == 4.00


def test_matches_openai_tier():
    result = estimate_cost("openai", "gpt-5-codex", 1_000_000, 1_000_000)
    assert result is not None
    assert result.input_usd == 1.25
    assert result.output_usd == 10.00


def test_matches_gemini_tier():
    result = estimate_cost("gemini", "gemini-3.5-flash", 1_000_000, 1_000_000)
    assert result is not None
    assert result.input_usd == 0.35
    assert result.output_usd == 1.05


def test_matching_is_case_insensitive():
    result = estimate_cost("anthropic", "Claude-SONNET-4-5-20250929", 1_000_000, 1_000_000)
    assert result is not None
    assert result.input_usd == 3.00


def test_unknown_provider_returns_none():
    assert estimate_cost("azure", "gpt-4", 1000, 1000) is None


def test_unmatched_model_within_known_provider_returns_none():
    assert estimate_cost("anthropic", "claude-instant-1.2", 1000, 1000) is None


def test_zero_tokens_returns_zero_cost_not_none():
    result = estimate_cost("anthropic", "claude-sonnet-4-5-20250929", 0, 0)
    assert result is not None
    assert result.input_usd == 0.0
    assert result.output_usd == 0.0
    assert result.total_usd == 0.0


def test_total_is_input_plus_output():
    result = estimate_cost("anthropic", "claude-sonnet-4-5-20250929", 500_000, 250_000)
    assert result is not None
    assert result.total_usd == result.input_usd + result.output_usd
    assert round(result.input_usd, 4) == 1.50
    assert round(result.output_usd, 4) == 3.75
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest backend/test_pricing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.pricing'`

- [ ] **Step 3: Write `backend/pricing.py`**

```python
from typing import NamedTuple, Optional


class ModelPrice(NamedTuple):
    input_per_1m_usd: float
    output_per_1m_usd: float


class EstimatedCost(NamedTuple):
    input_usd: float
    output_usd: float
    total_usd: float


# Keyed by provider, then an ordered list of (tier keyword, price) pairs.
# Matched as a case-insensitive substring of the run's `model` string: model
# strings are exact, dated/versioned IDs with no normalization anywhere in
# this codebase, so keyword matching survives new dated releases without a
# code change. First match wins, so order matters if keywords could overlap.
#
# NEEDS VERIFICATION against current official pricing before merging: these
# are best-effort placeholder figures, not confirmed current prices.
PRICING: dict[str, list[tuple[str, ModelPrice]]] = {
    "anthropic": [
        ("opus", ModelPrice(15.00, 75.00)),
        ("sonnet", ModelPrice(3.00, 15.00)),
        ("haiku", ModelPrice(0.80, 4.00)),
    ],
    "openai": [
        ("gpt-5-codex", ModelPrice(1.25, 10.00)),
    ],
    "gemini": [
        ("gemini-3.5-flash", ModelPrice(0.35, 1.05)),
    ],
}


def estimate_cost(
    provider: str, model: str, input_tokens: int, output_tokens: int
) -> Optional[EstimatedCost]:
    tiers = PRICING.get(provider)
    if not tiers:
        return None
    model_lower = model.lower()
    for keyword, price in tiers:
        if keyword in model_lower:
            input_usd = input_tokens / 1_000_000 * price.input_per_1m_usd
            output_usd = output_tokens / 1_000_000 * price.output_per_1m_usd
            return EstimatedCost(input_usd, output_usd, input_usd + output_usd)
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest backend/test_pricing.py -v`
Expected: `10 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/pricing.py backend/test_pricing.py
git commit -m "feat(AI-5): add pricing table and cost estimation"
```

---

### Task 2: Data model fields

**Files:**
- Modify: `backend/models.py:9-31` (`AgentRun`), `backend/models.py:58-75` (`AgentRunRead`)
- Test: `backend/test_models.py` (extend)

**Interfaces:**
- Produces:
  - `AgentRun.estimated_input_cost_usd: Optional[float] = None`
  - `AgentRun.estimated_output_cost_usd: Optional[float] = None`
  - `AgentRun.estimated_cost_usd: Optional[float] = None`
  - Same three fields on `AgentRunRead`

- [ ] **Step 1: Write the failing test**

Append to `backend/test_models.py` (read the existing file first to match its import style and fixture usage):

```python
def test_agent_run_cost_fields_default_to_none_and_round_trip(test_client):
    from sqlmodel import Session, select
    import backend.db as db_module
    from backend.models import AgentRun

    with Session(db_module.engine) as session:
        run = AgentRun(id="cost-test-run", provider="anthropic", model="m")
        session.add(run)
        session.commit()

        loaded = session.exec(select(AgentRun).where(AgentRun.id == "cost-test-run")).one()
        assert loaded.estimated_input_cost_usd is None
        assert loaded.estimated_output_cost_usd is None
        assert loaded.estimated_cost_usd is None

        loaded.estimated_input_cost_usd = 1.5
        loaded.estimated_output_cost_usd = 3.0
        loaded.estimated_cost_usd = 4.5
        session.add(loaded)
        session.commit()

        reloaded = session.exec(select(AgentRun).where(AgentRun.id == "cost-test-run")).one()
        assert reloaded.estimated_input_cost_usd == 1.5
        assert reloaded.estimated_output_cost_usd == 3.0
        assert reloaded.estimated_cost_usd == 4.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest backend/test_models.py -v`
Expected: FAIL — `AttributeError: 'AgentRun' object has no attribute 'estimated_input_cost_usd'`

- [ ] **Step 3: Add the fields**

In `backend/models.py`, insert after line 31 (`meta: dict = Field(default_factory=dict, sa_column=Column(JSON))`, still inside the `AgentRun` class, before the blank lines preceding `class TranscriptStore`):

```python
    estimated_input_cost_usd: Optional[float] = None
    estimated_output_cost_usd: Optional[float] = None
    estimated_cost_usd: Optional[float] = None
```

In the same file, insert after line 75 (`meta: dict = Field(default_factory=dict)`, inside `AgentRunRead`, before the end of the file):

```python
    estimated_input_cost_usd: Optional[float] = None
    estimated_output_cost_usd: Optional[float] = None
    estimated_cost_usd: Optional[float] = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest backend/test_models.py -v`
Expected: all tests in the file pass (including the pre-existing `User` round-trip test and the new one)

- [ ] **Step 5: Commit**

```bash
git add backend/models.py backend/test_models.py
git commit -m "feat(AI-5): add estimated cost fields to AgentRun and AgentRunRead"
```

---

### Task 3: Schema migration and historical backfill

**Files:**
- Modify: `backend/db.py:36-50` (`_add_missing_columns`), `backend/db.py:129-151` (end of `_seed`)
- Test: `backend/test_db.py` (new)

**Interfaces:**
- Consumes: `backend.pricing.estimate_cost` (Task 1), `AgentRun.estimated_input_cost_usd`/`estimated_output_cost_usd`/`estimated_cost_usd` (Task 2)

- [ ] **Step 1: Write the failing tests**

```python
from sqlmodel import Session, select

import backend.db as db_module
from backend.models import AgentRun


def _insert_raw_run(session: Session, **overrides) -> AgentRun:
    defaults = dict(
        id="raw-run", provider="anthropic", model="claude-sonnet-4-5-20250929",
        input_tokens=1_000_000, output_tokens=1_000_000,
    )
    defaults.update(overrides)
    run = AgentRun(**defaults)
    session.add(run)
    session.commit()
    return run


def test_add_missing_columns_adds_cost_columns_when_missing(test_client):
    from sqlalchemy import inspect, text

    # The test_client fixture's create_all() already creates these columns
    # on a fresh table, so simulate a genuine pre-migration production table
    # by dropping them first, then verify _add_missing_columns() actually
    # adds them back via its ALTER TABLE branch (SQLite 3.35+ supports
    # DROP COLUMN, matching the Python-bundled sqlite3 version in CI/dev).
    with db_module.engine.begin() as conn:
        conn.execute(text("ALTER TABLE agent_runs DROP COLUMN estimated_input_cost_usd"))
        conn.execute(text("ALTER TABLE agent_runs DROP COLUMN estimated_output_cost_usd"))
        conn.execute(text("ALTER TABLE agent_runs DROP COLUMN estimated_cost_usd"))

    db_module._add_missing_columns()

    inspector = inspect(db_module.engine)
    columns = {c["name"] for c in inspector.get_columns("agent_runs")}
    assert "estimated_input_cost_usd" in columns
    assert "estimated_output_cost_usd" in columns
    assert "estimated_cost_usd" in columns


def test_backfill_computes_cost_for_matched_historical_run(test_client):
    with Session(db_module.engine) as session:
        _insert_raw_run(session)

    db_module._seed()

    with Session(db_module.engine) as session:
        run = session.exec(select(AgentRun).where(AgentRun.id == "raw-run")).one()
        assert run.estimated_input_cost_usd == 3.00
        assert run.estimated_output_cost_usd == 15.00
        assert run.estimated_cost_usd == 18.00


def test_backfill_leaves_unmatched_model_as_none(test_client):
    with Session(db_module.engine) as session:
        _insert_raw_run(session, id="raw-run-unmatched", model="some-unknown-model")

    db_module._seed()

    with Session(db_module.engine) as session:
        run = session.exec(select(AgentRun).where(AgentRun.id == "raw-run-unmatched")).one()
        assert run.estimated_cost_usd is None


def test_backfill_is_idempotent(test_client):
    with Session(db_module.engine) as session:
        _insert_raw_run(session, id="raw-run-idempotent")

    db_module._seed()
    with Session(db_module.engine) as session:
        first_pass = session.exec(
            select(AgentRun).where(AgentRun.id == "raw-run-idempotent")
        ).one().estimated_cost_usd

    db_module._seed()
    with Session(db_module.engine) as session:
        second_pass = session.exec(
            select(AgentRun).where(AgentRun.id == "raw-run-idempotent")
        ).one().estimated_cost_usd

    assert first_pass == second_pass == 18.00
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest backend/test_db.py -v`
Expected: `test_add_missing_columns_adds_cost_columns_when_missing` FAILS (columns aren't re-added, since `_add_missing_columns()` doesn't know about them yet), and the three backfill tests FAIL with `AssertionError` (cost fields stay `None` since no backfill exists yet)

- [ ] **Step 3: Extend `_add_missing_columns()`**

In `backend/db.py`, replace the body of `_add_missing_columns()` (lines 36-50):

```python
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
```

- [ ] **Step 4: Add the backfill block to `_seed()`**

In `backend/db.py`, insert immediately after line 151 (`print(f"[db] backfilled ended_at for {len(stuck)} runs stuck done with null duration")`), still inside the `else:` branch at the same indentation (12 spaces):

```python
            # One-time backfill: existing rows already have model/input_tokens/
            # output_tokens stored, so cost can be computed retroactively,
            # unlike the ended_at backfill above where the source data for old
            # rows didn't exist. Only touches rows where estimated_cost_usd is
            # still NULL, so it's naturally idempotent and self-heals rows
            # whose model tier gets added to PRICING after they were ingested.
            from backend.pricing import estimate_cost

            uncosted = session.exec(
                select(AgentRun).where(AgentRun.estimated_cost_usd == None)  # noqa: E711
            ).all()
            if uncosted:
                for r in uncosted:
                    cost = estimate_cost(r.provider, r.model, r.input_tokens, r.output_tokens)
                    if cost:
                        r.estimated_input_cost_usd = cost.input_usd
                        r.estimated_output_cost_usd = cost.output_usd
                        r.estimated_cost_usd = cost.total_usd
                        session.add(r)
                session.commit()
                print(f"[db] backfilled estimated cost for {len(uncosted)} runs")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest backend/test_db.py -v`
Expected: `4 passed`

- [ ] **Step 6: Run the full backend suite to check for regressions**

Run: `uv run pytest backend/ -v`
Expected: all tests pass (existing suite plus the new ones from Tasks 1-3)

- [ ] **Step 7: Commit**

```bash
git add backend/db.py backend/test_db.py
git commit -m "feat(AI-5): add cost columns migration and historical backfill"
```

---

### Task 4: Ingest-time computation

**Files:**
- Modify: `backend/watcher.py`
- Test: `backend/test_watcher.py` (new)

**Interfaces:**
- Consumes: `backend.pricing.estimate_cost` (Task 1)

- [ ] **Step 1: Write the failing tests**

```python
from sqlmodel import Session, select

import backend.db as db_module
from backend.models import AgentRun
from backend.watcher import _upsert


def test_upsert_computes_and_stores_cost_on_insert(test_client):
    run = AgentRun(
        id="upsert-run", provider="anthropic", model="claude-sonnet-4-5-20250929",
        input_tokens=1_000_000, output_tokens=1_000_000,
    )
    assert _upsert(run) is True

    with Session(db_module.engine) as session:
        stored = session.exec(select(AgentRun).where(AgentRun.id == "upsert-run")).one()
        assert stored.estimated_input_cost_usd == 3.00
        assert stored.estimated_output_cost_usd == 15.00
        assert stored.estimated_cost_usd == 18.00


def test_upsert_recomputes_cost_on_update_as_tokens_grow(test_client):
    first = AgentRun(
        id="growing-run", provider="anthropic", model="claude-sonnet-4-5-20250929",
        input_tokens=500_000, output_tokens=500_000,
    )
    _upsert(first)

    grown = AgentRun(
        id="growing-run", provider="anthropic", model="claude-sonnet-4-5-20250929",
        input_tokens=1_000_000, output_tokens=1_000_000,
    )
    _upsert(grown)

    with Session(db_module.engine) as session:
        stored = session.exec(select(AgentRun).where(AgentRun.id == "growing-run")).one()
        assert stored.estimated_cost_usd == 18.00


def test_upsert_leaves_cost_none_for_unmatched_model(test_client):
    run = AgentRun(
        id="unmatched-run", provider="anthropic", model="some-unknown-model",
        input_tokens=1_000_000, output_tokens=1_000_000,
    )
    _upsert(run)

    with Session(db_module.engine) as session:
        stored = session.exec(select(AgentRun).where(AgentRun.id == "unmatched-run")).one()
        assert stored.estimated_cost_usd is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest backend/test_watcher.py -v`
Expected: FAIL — `AssertionError` (cost fields are `None` since `_upsert` doesn't compute them yet)

- [ ] **Step 3: Modify `_upsert()`**

In `backend/watcher.py`, add the import at the top of the file (after line 6, `from backend import sse`):

```python
from backend.pricing import estimate_cost
```

Replace the body of `_upsert()` (lines 37-56):

```python
def _upsert(run: AgentRun) -> bool:
    """Insert or update `run`. Returns False (no-op) for trivial/stub runs
    below MIN_TOKENS_TO_PERSIST, matching the ingest endpoint's behavior."""
    if run.input_tokens + run.output_tokens < MIN_TOKENS_TO_PERSIST:
        return False
    cost = estimate_cost(run.provider, run.model, run.input_tokens, run.output_tokens)
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest backend/test_watcher.py -v`
Expected: `3 passed`

- [ ] **Step 5: Run the full backend suite to check for regressions**

Run: `uv run pytest backend/ -v`
Expected: all tests pass, no regressions

- [ ] **Step 6: Commit**

```bash
git add backend/watcher.py backend/test_watcher.py
git commit -m "feat(AI-5): compute cost on ingest via _upsert"
```

---

### Task 5: Stats endpoint total cost

**Files:**
- Modify: `backend/api/routes.py:246-277` (`get_stats`)
- Test: `backend/api/test_stats.py` (new)

**Interfaces:**
- Consumes: `AgentRun.estimated_cost_usd` (Task 2)
- Produces: `GET /api/stats` response gains `"total_cost_usd": float`

- [ ] **Step 1: Write the failing tests**

```python
from sqlmodel import Session

import backend.db as db_module
from backend.auth import hash_password
from backend.models import AgentRun, User


def _login(client, username: str, password: str):
    res = client.post("/api/login", json={"username": username, "password": password})
    assert res.status_code == 200


def test_stats_total_cost_sums_matched_runs_and_skips_none(test_client, monkeypatch):
    import backend.api.auth_routes as auth_routes_module
    monkeypatch.setattr(auth_routes_module, "_COOKIE_SECURE", False)

    with Session(db_module.engine) as session:
        session.add(User(username="alice", password_hash=hash_password("x"), is_admin=True))
        session.add(AgentRun(
            id="run-costed-1", provider="anthropic", model="claude-sonnet-4-5-20250929",
            input_tokens=1_000_000, output_tokens=1_000_000,
            estimated_cost_usd=18.00, estimated_input_cost_usd=3.00, estimated_output_cost_usd=15.00,
            user="alice",
        ))
        session.add(AgentRun(
            id="run-costed-2", provider="anthropic", model="claude-haiku-4-5-20251001",
            input_tokens=1_000_000, output_tokens=1_000_000,
            estimated_cost_usd=4.80, estimated_input_cost_usd=0.80, estimated_output_cost_usd=4.00,
            user="alice",
        ))
        session.add(AgentRun(
            id="run-uncosted", provider="anthropic", model="unknown-model",
            input_tokens=1_000_000, output_tokens=1_000_000,
            user="alice",
        ))
        session.commit()

    _login(test_client, "alice", "x")
    res = test_client.get("/api/stats")
    assert res.status_code == 200
    assert res.json()["total_cost_usd"] == 22.80


def test_stats_total_cost_is_zero_when_no_runs_have_cost(test_client, monkeypatch):
    import backend.api.auth_routes as auth_routes_module
    monkeypatch.setattr(auth_routes_module, "_COOKIE_SECURE", False)

    with Session(db_module.engine) as session:
        session.add(User(username="bob", password_hash=hash_password("y"), is_admin=True))
        session.commit()

    _login(test_client, "bob", "y")
    res = test_client.get("/api/stats")
    assert res.status_code == 200
    assert res.json()["total_cost_usd"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest backend/api/test_stats.py -v`
Expected: FAIL — `KeyError: 'total_cost_usd'`

- [ ] **Step 3: Modify `get_stats()`**

In `backend/api/routes.py`, replace the returned dict (lines 259-277):

```python
    return {
        "total_runs_7d": len(recent),
        "total_input_tokens_7d": sum(r.input_tokens for r in recent),
        "total_output_tokens_7d": sum(r.output_tokens for r in recent),
        "total_commits_7d": sum(len(r.git_commits) for r in recent),
        "total_prs_7d": sum(len(r.git_prs) for r in recent),
        "total_cost_usd": sum(
            r.estimated_cost_usd for r in recent if r.estimated_cost_usd is not None
        ),
        "days": days,
        "active_providers": list({r.provider for r in recent}),
        "running_count": sum(1 for r in runs if r.status == "running"),
        "by_provider": {
            p: {
                "runs": sum(1 for r in recent if r.provider == p),
                "input_tokens": sum(r.input_tokens for r in recent if r.provider == p),
                "output_tokens": sum(r.output_tokens for r in recent if r.provider == p),
                "commits": sum(len(r.git_commits) for r in recent if r.provider == p),
            }
            for p in PROVIDERS
        },
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest backend/api/test_stats.py -v`
Expected: `2 passed`

- [ ] **Step 5: Run the full backend suite to check for regressions**

Run: `uv run pytest backend/ -v`
Expected: all tests pass, no regressions

- [ ] **Step 6: Commit**

```bash
git add backend/api/routes.py backend/api/test_stats.py
git commit -m "feat(AI-5): add total_cost_usd to /api/stats"
```

---

### Task 6: Frontend types

**Files:**
- Modify: `frontend/src/lib/types.ts:1-19` (`AgentRun`), `frontend/src/lib/types.ts:37-46` (`Stats`)

**Interfaces:**
- Produces:
  - `AgentRun.estimated_input_cost_usd: number | null`
  - `AgentRun.estimated_output_cost_usd: number | null`
  - `AgentRun.estimated_cost_usd: number | null`
  - `Stats.total_cost_usd: number`

- [ ] **Step 1: Add the fields**

In `frontend/src/lib/types.ts`, insert into the `AgentRun` interface, after line 18 (`meta: { ... };`) and before the closing `}` (line 19):

```typescript
  estimated_input_cost_usd: number | null;
  estimated_output_cost_usd: number | null;
  estimated_cost_usd: number | null;
```

Insert into the `Stats` interface, after line 42 (`total_prs_7d: number;`):

```typescript
  total_cost_usd: number;
```

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors (additive optional-shaped fields not yet consumed anywhere)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/types.ts
git commit -m "feat(AI-5): add estimated cost fields to frontend types"
```

---

### Task 7: Dashboard stat card

**Files:**
- Modify: `frontend/src/pages/Dashboard.tsx:137-147`

**Interfaces:**
- Consumes: `Stats.total_cost_usd` (Task 6)

- [ ] **Step 1: Add the stat card**

In `frontend/src/pages/Dashboard.tsx`, replace the stat-card grid (lines 137-147):

```tsx
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatCard label="Runs" value={stats?.total_runs_7d ?? "—"} accent="#8b5cf6" />
        <StatCard
          label="Tokens"
          value={stats ? fmt(totalTokens) : "—"}
          sub={stats ? `${fmt(stats.total_input_tokens_7d)} in · ${fmt(stats.total_output_tokens_7d)} out` : undefined}
          accent="#f59e0b"
        />
        <StatCard label="Commits" value={stats?.total_commits_7d ?? "—"} accent="#22c55e" />
        <StatCard label="PRs Opened" value={stats?.total_prs_7d ?? "—"} accent="#3b82f6" />
        <StatCard
          label="Est. Spend"
          value={stats ? `$${stats.total_cost_usd.toFixed(2)}` : "—"}
          sub="estimated; pricing may change"
          accent="#ec4899"
        />
      </div>
```

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors

- [ ] **Step 3: Manual verification**

With the backend and frontend dev servers running (`uv run uvicorn backend.main:app --reload` and `npm run dev` from `frontend/`) and at least one run in the DB with a matched model (e.g. any Claude Code session, which will match the `sonnet` tier if using a Sonnet model), visit `/` and confirm a fifth "Est. Spend" stat card renders with a dollar figure and the "estimated; pricing may change" sub-label.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/Dashboard.tsx
git commit -m "feat(AI-5): add estimated spend stat card to dashboard"
```

---

### Task 8: Run detail cost breakdown

**Files:**
- Modify: `frontend/src/pages/RunDetail.tsx:114-141`

**Interfaces:**
- Consumes: `AgentRun.estimated_input_cost_usd`/`estimated_output_cost_usd`/`estimated_cost_usd` (Task 6)

- [ ] **Step 1: Add the cost card**

In `frontend/src/pages/RunDetail.tsx`, insert immediately after line 140 (the closing `</div>` of the existing "Tokens" card) and before line 141 (the closing `</div>` of the outer page container):

```tsx

      <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
        <p className="text-xs text-slate-500 font-mono uppercase tracking-wider mb-3">Estimated Cost</p>
        {run.estimated_cost_usd != null ? (
          <div className="grid grid-cols-3 gap-4 text-center">
            <div>
              <p className="text-xl font-mono font-semibold text-slate-100">${run.estimated_input_cost_usd!.toFixed(4)}</p>
              <p className="text-xs text-slate-500 mt-0.5">input</p>
            </div>
            <div>
              <p className="text-xl font-mono font-semibold text-slate-100">${run.estimated_output_cost_usd!.toFixed(4)}</p>
              <p className="text-xs text-slate-500 mt-0.5">output</p>
            </div>
            <div>
              <p className="text-xl font-mono font-semibold text-slate-100">${run.estimated_cost_usd.toFixed(4)}</p>
              <p className="text-xs text-slate-500 mt-0.5">total</p>
            </div>
          </div>
        ) : (
          <p className="text-sm text-slate-600">Unknown model, no pricing data</p>
        )}
      </div>
```

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors

- [ ] **Step 3: Manual verification**

Visit `/runs/:id` for a run with a matched model and confirm the new "Estimated Cost" card renders input/output/total dollar figures below the existing "Tokens" card. Visit a run with an unmatched model (or manually null the cost fields via a DB query) and confirm the "Unknown model, no pricing data" fallback message renders instead of `$NaN` or a blank value.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/RunDetail.tsx
git commit -m "feat(AI-5): add estimated cost breakdown to run detail"
```

---

## Post-plan verification

- [ ] Run the full backend suite once more: `uv run pytest backend/ -v` — all tests pass.
- [ ] Run `cd frontend && npx tsc --noEmit && npm run build` — both succeed.
- [ ] Manually verify the dashboard stat card and run-detail breakdown against at least one real ingested run per provider (Claude Code, Codex CLI, Gemini CLI) to confirm each provider's tier actually matches and displays a cost.
