# AI-5: Cost tracking: estimated $ spend per run and on dashboard

## Context

The dashboard tracks token usage per run (`AgentRun.input_tokens`/`output_tokens`) but has no
concept of dollar cost anywhere: `grep`ing the repo for pricing/cost/`$`-related logic turns up
nothing. Runs are ingested from three providers (Claude Code/Anthropic, Codex CLI/OpenAI, Gemini
CLI/Gemini), each with its own model string extracted verbatim from the transcript (e.g. a versioned
Anthropic model ID, or literal strings like `"gpt-5-codex"`/`"gemini-3.5-flash"`), and none of these
strings are normalized anywhere in the codebase today.

Both ingest paths (the API-key `POST /api/v1/ingest` endpoint used by the collector, and the local
`watcher.py` filesystem-scan path used for local dev) converge on one shared function,
`backend/watcher.py`'s `_upsert()`, before writing to the DB.

### DoD (from Linear AI-5)
- Pricing table in backend for Anthropic models (Sonnet, Opus, Haiku) with input/output $/1M tokens
- `estimated_cost_usd` computed on ingest and stored on `AgentRun`
- Dashboard stat card shows total $ for selected time range
- Run detail shows input cost + output cost + total
- Clearly labeled "estimated" since pricing can change

## Key design decisions

Resolved during brainstorming; each has a real alternative, so called out explicitly:

1. **Provider scope extended beyond the literal DoD**: the DoD only asks for Anthropic pricing, but
   OpenAI and Gemini adapters have shipped since this ticket was written. Covering all three now
   avoids a dashboard-wide "$ spend" stat card that silently undercounts 2 of 3 providers.
2. **Model-to-price matching is tier-based substring matching, not exact string matching.** Model
   strings are exact, dated/versioned IDs with no normalization anywhere in this codebase. Matching
   on a tier keyword (e.g. any string containing `"sonnet"`) survives new dated model releases
   without a code change, at the cost of being approximate if a future model tier reuses an old
   tier's keyword but is priced differently.
3. **Unmatched models get `null` cost, not a fallback estimate.** A run whose model string doesn't
   match any known tier is excluded from cost display and from `total_cost_usd` sums, rather than
   guessing with a default price. Avoids silently mis-stating spend.
4. **Historical rows get a one-time backfill, not forward-only computation.** Unlike a past case in
   this codebase (`ended_at` backfill, where the source data for old rows didn't exist), every
   existing run already has `model`/`input_tokens`/`output_tokens` stored, so retroactive
   computation is possible and worth doing; otherwise the dashboard's $ totals would silently
   undercount for any time range spanning pre-feature runs.
5. **Pricing table is a hardcoded Python constant, computed once at ingest (not a config file, not
   an admin-editable DB table).** Matches the DoD's literal "pricing table in backend" wording, fits
   this codebase's existing style (no Alembic, hand-rolled migrations, plain Python constants), and
   keeps the "estimated, can change" framing honest: updating prices means a code change + redeploy
   + re-running the backfill, appropriate for something explicitly labeled an estimate rather than
   precision billing.
6. **Cost is recomputed on every `_upsert`, not just first insert.** A still-`running` session's
   token counts grow as it progresses; recomputing on every update means its cost estimate grows
   with it instead of freezing at whatever it was on first ingest.
7. **The backfill also self-heals unmatched rows.** It only touches rows where
   `estimated_cost_usd IS NULL`, so a run that couldn't be matched at ingest time (e.g. because its
   model tier was added to the pricing table *after* that run was ingested) gets picked up
   automatically the next time the backfill runs (every startup), with no separate "recompute costs"
   mechanism needed. The tradeoff: correcting an already-matched run's *price* (not adding a new
   tier) does NOT retroactively update it, since its `estimated_cost_usd` is already non-null. See
   "Known limitation" below.

## Data model

Three new optional fields on `AgentRun` (`backend/models.py`), computed together in a single call so
they can never drift out of sync with each other; always either all three are set, or all three are
`None`:

```python
estimated_input_cost_usd: Optional[float] = None
estimated_output_cost_usd: Optional[float] = None
estimated_cost_usd: Optional[float] = None   # = estimated_input_cost_usd + estimated_output_cost_usd
```

Mirrored on `AgentRunRead` so they're exposed via the API (no handler changes needed beyond adding
the fields, since `_to_read()` already does `run.model_dump()`).

These are columns on the *existing* `agent_runs` table, so, matching this codebase's established
pattern (no Alembic; `SQLModel.metadata.create_all()` only creates missing tables, never alters
existing ones), they need the same hand-rolled `ALTER TABLE` treatment `backend/db.py`'s
`_add_missing_columns()` already uses for the existing `updated_at` column.

## Pricing table & cost estimation

New file `backend/pricing.py` is a pure module with no DB/network access, trivially unit-testable:

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

The "NEEDS VERIFICATION" comment stays in the source itself, not just this doc, so anyone reading the
code sees the pricing hasn't been independently confirmed against official pricing pages.

## Ingest-time computation

Hooked into `_upsert()` in `backend/watcher.py`, the single choke point both the API-key ingest path
(`backend/api/routes.py`'s `ingest_transcript`) and the local watcher path already share:

```python
def _upsert(run: AgentRun) -> bool:
    if run.input_tokens + run.output_tokens < MIN_TOKENS_TO_PERSIST:
        return False
    cost = estimate_cost(run.provider, run.model, run.input_tokens, run.output_tokens)
    if cost:
        run.estimated_input_cost_usd = cost.input_usd
        run.estimated_output_cost_usd = cost.output_usd
        run.estimated_cost_usd = cost.total_usd
    with Session(engine) as session:
        ...  # existing insert/update logic, unchanged
```

## Historical backfill

Following the existing `_seed()` pattern in `backend/db.py` (same file that already does one-time
repairs like the `ended_at` backfill for stuck-`done` rows), a new one-time block runs on every
startup:

```python
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

Naturally idempotent (only touches rows where `estimated_cost_usd IS NULL`) and self-healing for
newly-added pricing tiers (see decision #7), but see "Known limitation" below for what it does *not*
fix.

## API changes

**`GET /api/stats`** gets one new field, following the exact `sum(...)` idiom already used for
tokens/commits, skipping `None`s:

```python
"total_cost_usd": sum(
    r.estimated_cost_usd for r in recent if r.estimated_cost_usd is not None
),
```

**`GET /api/runs`** and **`GET /api/runs/:id`** automatically expose the three new fields once added
to `AgentRunRead`; no handler changes needed.

**`GET /api/daily`** is explicitly untouched: out of scope per the DoD (stat card + run-detail
breakdown only, no daily cost chart).

## Frontend

**`Dashboard.tsx`**: one new `StatCard` in the existing 4-card grid, reading `stats.total_cost_usd`:

```tsx
<StatCard
  label="Est. Spend"
  value={stats ? `$${stats.total_cost_usd.toFixed(2)}` : "—"}
  sub="estimated; pricing may change"
  accent="#ec4899"
/>
```

**`RunDetail.tsx`**: a new card mirroring the existing "Tokens" card's 3-column (input/output/total)
structure, placed as a sibling right after it:

```tsx
<div className="bg-slate-900 border border-slate-800 rounded-lg p-5">
  <p className="text-xs text-slate-500 font-mono uppercase tracking-wider mb-4">
    Estimated Cost
  </p>
  {run.estimated_cost_usd != null ? (
    <div className="grid grid-cols-3 gap-4">
      <div><p className="text-xs text-slate-500">Input</p><p>${run.estimated_input_cost_usd.toFixed(4)}</p></div>
      <div><p className="text-xs text-slate-500">Output</p><p>${run.estimated_output_cost_usd.toFixed(4)}</p></div>
      <div><p className="text-xs text-slate-500">Total</p><p>${run.estimated_cost_usd.toFixed(4)}</p></div>
    </div>
  ) : (
    <p className="text-sm text-slate-600">Unknown model: no pricing data</p>
  )}
</div>
```

`AgentRun` and `Stats` TS interfaces (`frontend/src/lib/types.ts`) get the three new optional cost
fields and `total_cost_usd` respectively.

## Error handling & edge cases

- **Unmatched model** → all three cost fields stay `None`; excluded from `total_cost_usd`; UI shows
  "Unknown model: no pricing data" instead of a dollar figure.
- **Zero tokens of one kind** → `estimate_cost` still returns a valid result with `0.0` for that
  side: no special-casing needed, the arithmetic just works.
- **Known limitation: correcting an already-matched price doesn't retroactively fix old rows.**
  The backfill only touches rows where `estimated_cost_usd IS NULL`. If a price in `PRICING` is
  *corrected* (not newly added) after some runs already have a stored cost from the old price, those
  runs keep the stale value; the backfill's null-check won't re-touch them. Forcing a full
  recompute after a price correction would require a manual one-off (e.g. nulling the columns
  first); this design does not build a dedicated mechanism for that, consistent with treating this
  as a rough estimate rather than precision billing.

## Testing

- **`backend/test_pricing.py`** (new, pure unit tests, no DB): tier-keyword matching per provider,
  case-insensitivity, unmatched provider → `None`, unmatched model within a known provider → `None`,
  zero-token inputs, arithmetic correctness (input + output = total).
- **`backend/test_watcher.py`** (new): `_upsert` computes and stores cost on insert; recomputes on
  update as tokens grow; leaves fields `None` for an unmatched model.
- **`backend/test_db.py`** (new): backfill populates cost for pre-existing rows with matched models,
  leaves unmatched ones `None`, is idempotent (running twice doesn't change already-computed values).
- **`backend/api/test_routes.py`** (extend) or a new file: `total_cost_usd` in `/api/stats` sums
  correctly and skips `None`s.
- **Frontend**: no test runner in this repo (established constraint, not introduced here); manual
  verification: seed a run with a known model/token count, confirm the dashboard stat card and
  run-detail breakdown show the expected dollar figures; confirm an unmatched-model run shows the
  fallback message instead of `$NaN` or a blank value.

## Out of scope

- Per-day cost in the `/api/daily` chart (DoD only asks for the stat card + run-detail breakdown).
- Admin-editable pricing (Settings UI or DB-backed pricing table): hardcoded Python constant only.
- Automatic re-pricing of historical rows when an existing tier's price is corrected (see "Known
  limitation" above); only newly-added tiers get picked up by the backfill.
- Non-USD currencies.
