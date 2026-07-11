# Delta-based daily/stats attribution

## Context

`19c447a` ("Fix /daily and /stats to not hide ongoing activity from long-running sessions") fixed
the symptom — a running session that had aged past its start day used to vanish from `/daily` and
drop out of `/stats`'s totals — but the fix re-attributes a running session's *entire, cumulative*
token/cost total to "today" on every poll, then snaps it back to `started_at`'s day the moment the
session finishes. Two concrete problems follow, both provable from the code (`backend/api/routes.py`
`get_daily`/`get_stats`):

1. **Non-reproducible history.** `bucket_date = datetime.utcnow() if r.status == "running" else
   r.started_at` means the same past date, queried on different days while a session is still
   running, returns different numbers — and once the session finishes, days it was previously
   attributed to silently zero out as the total reattaches to the start day. `/daily` is not a
   stable historical record.
2. **Magnitude inflation.** A session that has accrued N days' worth of tokens reports as "N days'
   tokens generated today," every day it's still running — not the state, real, per-day amount. Two
   real sessions have been running continuously since 2026-07-06, so this is live in production, not
   hypothetical.

A secondary, smaller issue in the same commit: `/stats`'s `days` query parameter silently stopped
meaning "activity in the last N days" — `recent = [... or r.status == "running"]` includes a
running session regardless of how long ago it started, so `total_cost_usd`/`total_runs_7d`/commit
and PR counts can include a session that's been running for weeks even when `days=1` is requested.

## Design

Replace run-level `started_at`/`status` branching in `/daily` and `/stats` with a per-day delta
ledger, so a day's bucket is written once, from that day's actual token growth, and never revisited.

### Schema

New table, created automatically by `SQLModel.metadata.create_all()` (no `ALTER TABLE` needed since
it doesn't exist yet):

```python
class RunDailyUsage(SQLModel, table=True):
    __tablename__ = "run_daily_usage"
    run_id: str = Field(foreign_key="agent_runs.id", primary_key=True)
    date: str = Field(primary_key=True)   # "YYYY-MM-DD", UTC
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
```

### Delta computation

Both the local watcher (`backend/watcher.py`'s file-watch loop) and the remote `/v1/ingest` endpoint
already funnel every upsert through the single `watcher._upsert()` function — that's the one place
to compute deltas. On each upsert, after the existing cost estimate is computed:

- `already_attributed_input = sum(row.input_tokens for row in existing RunDailyUsage rows for run_id)`
  (same for output/cost).
- `delta_input = run.input_tokens - already_attributed_input` (same for output/cost); each is
  clamped to `>= 0` — a transcript should only grow, but this guards against a re-parse producing a
  smaller number than previously recorded without corrupting the ledger with a negative entry.
- Get-or-create today's `(run_id, today)` row and add the deltas into it (a run polled multiple
  times in one day accumulates into the same row).
- Per the resolved design question, the delta is attributed to the day of *this poll* — not
  reconstructed from the transcript's per-message timestamps. If the collector misses a day (e.g.
  laptop asleep) and catches up later, the catch-up delta lands on the catch-up day, not the days it
  actually happened. For a brand-new run, `already_attributed` is naturally 0, so its first delta
  (the whole thing) lands on its actual start day — identical to current behavior for the common
  case of a normal, promptly-polled run.

### Query rewrite

- **`/daily`**: query `RunDailyUsage` rows with `date` in the requested range, grouped by `date`.
  Sum `input_tokens`/`output_tokens` per bucket; increment a provider's per-day count once per
  distinct `run_id` that has a nonzero-delta row that day (preserves the existing "number of active
  runs of this provider" meaning of that field, now correctly counting a multi-day run as active on
  each day it actually grew, not just its start day or "today"). Visibility/user filtering
  (`_visible_runs`) still applies — join back to `AgentRun` to filter by the caller's visible run
  ids before aggregating.
- **`/stats`**: redefine "recent" as *runs with at least one `RunDailyUsage` row whose `date` falls
  in `[cutoff_date, today]`*, replacing `started_at >= cutoff or status == "running"`. This also
  fixes the secondary issue: a stale running session with no ledger activity in the requested window
  no longer inflates `total_cost_usd`/`total_runs_7d`/commit/PR counts. `running_count` (count of all
  currently-running sessions, unwindowed) is unchanged — it was never part of either bug.

### Backfill

Following `backend/db.py`'s existing convention (`_backfill_cached_input_tokens`,
`_backfill_ticket_refs`, both called from `_seed()`'s "not first deploy" branch): add
`_backfill_run_daily_usage(session)`, run in the same place. For every `AgentRun` with no existing
`RunDailyUsage` rows, insert one row dated at `started_at`'s date with the run's current cumulative
`input_tokens`/`output_tokens`/`estimated_cost_usd`. This covers the two sessions running since
2026-07-06: their already-accrued totals get one lump on their start day (matching current behavior
— true historical deltas for time already elapsed can't be recovered), and all growth from the first
post-deploy poll onward is attributed correctly, day by day. Idempotent (guarded on "no existing rows
for this run_id"), matching this file's established backfill style.

## Testing plan

- Extend `backend/api/test_daily.py` and `backend/api/test_stats.py` with a scenario the current
  tests don't cover: ingest a running session (creating its first `RunDailyUsage` row), advance
  "today" (via `monkeypatch` on the relevant `datetime.utcnow` call sites), ingest again with a
  larger cumulative token count, and assert (a) the first day's bucket is unchanged by the second
  ingest, and (b) the second day's bucket reflects only the delta, not the full cumulative total.
- Keep the existing three tests from `19c447a` (running session buckets on today, done session
  stays on its start day, running session stays visible outside its own start window) — all three
  still hold under the new model and remain valid regression coverage.
- Add a `backend/test_db.py` (or wherever `_seed()`'s other backfill blocks are tested) case seeding
  a pre-existing `AgentRun` with no `RunDailyUsage` rows, running the backfill, and asserting a
  single seeded row dated at `started_at` with the run's current totals; a second run of the backfill
  must be a no-op (idempotency).
- **Live verification**: after deploying, confirm via `/api/daily` that the two real long-running
  sessions from 2026-07-06 show a one-time backfilled lump on 07-06 and then correct, small per-day
  deltas going forward instead of their full total re-appearing under "today" each day.

## Out of scope

- Reconstructing exact per-day attribution from transcript message timestamps (considered and
  explicitly rejected for this fix per user decision — poll-day attribution is the agreed
  precision level; revisit only if poll-day granularity proves materially wrong in practice).
- Fixing `total_commits_7d`/`total_prs_7d`/`by_provider.commits` beyond the window-membership fix
  above — these remain counts over the (now correctly windowed) "recent" run list; per-day
  attribution of individual commits/PRs (which have no per-day ledger, only membership in a run)
  is not part of this change.
- Any frontend/dashboard display changes — this is a backend data-correctness fix only.
