# Fix input-token accounting to exclude cached/re-sent context

## Context

Comparing real production token data across all three providers surfaced a serious accounting
inconsistency: `backend/adapters/codex.py` and `backend/adapters/gemini_cli.py` both currently
report `input_tokens` in a way that includes cached (re-sent) context tokens, summed or taken
cumulatively across every turn of a session. `backend/adapters/claude_code.py`'s `input_tokens`,
by contrast, already reflects only fresh/non-cached tokens per Anthropic's API semantics (its
`usage.input_tokens` field never included cache reads or cache-creation writes).

Verified via direct analysis of real transcript files on this machine (not synthetic data):
excluding cached tokens from Codex's and Gemini's methodology reduces their reported input totals
by ~74–75% each, bringing them into the same order of magnitude as Codex's and Gemini's own "new
tokens only" figures, and making the three providers' `input_tokens` columns finally mean the same
thing. This fix is a prerequisite for AI-5 (cost tracking) — accurate $ cost requires knowing which
tokens were fresh (full price) vs. cached (discounted), and it's also a precondition for drawing any
honest conclusions about usage patterns (shorter sessions, clearing context, etc.) across providers.

## Root cause per adapter

- **Codex:** `event_msg`/`token_count` events carry `info.total_token_usage` (a cumulative running
  total including cached tokens) and `info.last_token_usage` (that turn's own delta, itself
  including cached tokens, with a `cached_input_tokens` breakdown). The adapter currently takes the
  *last* `total_token_usage.input_tokens` value directly — a cumulative sum of every turn's full
  (cached-inclusive) context, verified against real data to be conceptually identical to Gemini's
  bug, not merely superficially similar.
- **Gemini CLI:** each `"gemini"`-type event's `tokens.input` is summed across every turn, and
  `tokens.cached` is a subset of `tokens.input` (verified: `total = input + output + thoughts +
  tool`, with `cached` not additive). Summing raw `input` re-counts the same cached prefix on every
  turn.
- **Claude Code:** `usage.input_tokens` per assistant message already excludes both
  `cache_read_input_tokens` and `cache_creation_input_tokens` — no fix needed to `input_tokens`
  itself.

## Fix per adapter

**`backend/adapters/codex.py`:** sum `last_token_usage.input_tokens - last_token_usage.cached_input_tokens`
across turns instead of taking the last cumulative `total_token_usage.input_tokens` value. Verified
against real data that `token_count` events are logged twice consecutively (same artifact already
handled in `gemini_cli.py`, but Codex's events carry no `id` field to dedupe by) — dedupe here by
tracking the last-seen `total_token_usage` tuple and only processing an event when it has actually
changed from the previous one. Also sum `last_token_usage.cached_input_tokens` into a new
`meta.cached_input_tokens`.

**`backend/adapters/gemini_cli.py`:** change `input_tokens += tokens.get('input', 0)` to
`input_tokens += tokens.get('input', 0) - tokens.get('cached', 0)`. Sum `tokens.get('cached', 0)`
into `meta.cached_input_tokens` — already dedup-safe via the adapter's existing `seen_ids` set.

**`backend/adapters/claude_code.py`:** no change to `input_tokens`. Add `meta.cached_input_tokens`
= sum of `usage.get('cache_read_input_tokens', 0)` per assistant message, reusing the existing
`seen_request_ids` dedup set. `cache_creation_input_tokens` (the cost of *writing* new cache
entries — a premium-priced, fresh-content category, not a discounted-reuse one) is deliberately NOT
captured yet; it's a different economic category that AI-5 should price separately if it matters,
not an oversight.

## Backfill

Historical rows: 23 real Claude Code runs need no `input_tokens` change (already correct) but should
get `meta.cached_input_tokens` backfilled for consistency. 9 real Codex runs need both `input_tokens`
corrected and `meta.cached_input_tokens` added. No Gemini rows currently exist in production (the
only 15 that existed were test data, already removed) — new ones will use the fixed logic from
first ingest, no backfill needed for that provider.

Follows this project's existing convention for this exact kind of fix: `backend/db.py`'s `_seed()`
function already contains several idempotent, inline backfill/cleanup blocks that run automatically
on every backend startup (e.g. the `updated_at` backfill for stuck-done runs, the demo-row cleanup,
the malformed `git_commits`/`git_prs` cleanup) — this fix adds one more block in the same style,
rather than introducing a new migration mechanism:

- For every `AgentRun` with `provider="openai"` (Codex) that doesn't yet have
  `meta.cached_input_tokens` set: look up its `TranscriptStore` row by id, re-run the stored content
  through the fixed `codex.parse_transcript_content()`, and update `input_tokens`/`meta` in place
  from the result.
- For every `AgentRun` with `provider="anthropic"` (Claude Code) missing `meta.cached_input_tokens`:
  same re-parse-and-update approach via `claude_code.parse_transcript_content()`, but only `meta`
  changes (`input_tokens` is unaffected, so no risk of it changing under this backfill).
- Guarded by checking for the presence of `meta.cached_input_tokens` already being set, so this is
  safe to run on every startup (idempotent — already-migrated rows are skipped, matching the
  existing blocks' guard style in `_seed()`).
- A row whose `TranscriptStore` entry is missing (shouldn't normally happen, but matches this
  codebase's defensive style elsewhere) is skipped, not treated as an error.

## Testing plan

- **`backend/adapters/test_codex.py`**: update the existing synthetic fixture's token-count event
  pair to include nonzero `cached_input_tokens` on the second event, and add a duplicate-event
  (logged-twice) case; assert `input_tokens` reflects the summed *new-only* delta (not the old
  last-cumulative-value behavior) and `meta.cached_input_tokens` reflects the summed cached delta,
  with the duplicate event correctly not double-counted.
- **`backend/adapters/test_gemini_cli.py`**: update the existing fixture's `tokens` dicts to include
  nonzero `cached` values; assert `input_tokens` excludes it and `meta.cached_input_tokens` sums it.
- **`backend/adapters/test_common.py`** or a new **`backend/adapters/test_claude_code.py`** (none
  exists yet for this adapter — this would be its first test file): assert
  `meta.cached_input_tokens` sums `cache_read_input_tokens` correctly with the existing dedup.
- **`backend/test_db.py`** (or wherever `_seed()`'s existing backfill blocks are tested, if they are
  — check first): a test seeding a pre-fix-shaped Codex `AgentRun` + matching `TranscriptStore`,
  running `init_db()`/`_seed()`, and asserting the row's `input_tokens`/`meta` were corrected; and a
  second run confirming idempotency (no further change, no error) on an already-migrated row.
- **Live verification**: after deploying, confirm via `/api/runs?provider=openai` that the 9 real
  Codex rows show reduced `input_tokens` and a populated `meta.cached_input_tokens`, and that a
  freshly-ingested Codex or Gemini session shows the corrected accounting from the start.

## Out of scope

- AI-5's actual cost/$ calculation logic — this ticket only fixes the token *counts* and captures
  the cache breakdown needed for that future work.
- Claude's `cache_creation_input_tokens` (premium-priced fresh writes) — deliberately deferred, see
  above.
- Any frontend/dashboard display changes — this is a backend data-correctness fix only.
