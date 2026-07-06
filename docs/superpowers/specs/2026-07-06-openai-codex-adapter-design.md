# AI-46: OpenAI (Codex CLI) transcript ingestion adapter

## Context

The README and dashboard both advertise support for "Claude Code, OpenAI, and Gemini"
(`backend/api/routes.py`'s `PROVIDERS = ("anthropic", "openai", "gemini")` is already wired into
every aggregate/bucket endpoint), but `backend/adapters/` only contains `claude_code.py`. No
OpenAI or Gemini coding-agent session has ever actually been ingested — those provider buckets are
permanently zero.

This design adds the first non-Claude-Code adapter, for **Codex CLI** (confirmed installed and
configured on this machine at `~/.codex/`), and generalizes the collector/backend pipeline so a
third provider (Gemini, a separate ticket — AI-47) can be added later without further pipeline
changes.

Every Codex CLI session is labeled `provider="openai"` regardless of which backend model actually
served it — Codex CLI supports routing through local Ollama or OpenRouter profiles (even to
non-OpenAI models), but the adapter doesn't try to detect or special-case that; it labels by
*tool*, matching how `claude_code.py` already labels every session `provider="anthropic"`
regardless of which specific Claude model was used.

## Architecture & multi-provider dispatch

```
Collector (collector/collector.py)
   SOURCES = {
     "anthropic": ~/.claude/projects,
     "openai":    ~/.codex/sessions,
     # "gemini": <path>  ← added later (AI-47), same shape, no dispatch changes
   }
   │
   ├─ watches/syncs each source's *.jsonl files independently
   └─ ships each file's content with header X-Provider: <source's key>
              │
              ▼
   POST /api/v1/ingest  (backend/api/routes.py)
   PROVIDER_ADAPTERS = {
     "anthropic": claude_code.parse_transcript_content,
     "openai":    codex.parse_transcript_content,
   }
   parse_fn = PROVIDER_ADAPTERS.get(x_provider, claude_code.parse_transcript_content)
   run = parse_fn(content, mtime=x_file_mtime)
```

The collector's single `TRANSCRIPTS_BASE` constant generalizes into a `SOURCES` dict of
`{provider: base_dir}`; the same sync/watch logic runs per source, and each source independently
no-ops if its directory doesn't exist (matching today's single-source behavior — a machine without
Codex installed just never syncs that source). The shipped `X-Provider` header tells the backend
which adapter to use; it defaults to `"anthropic"` server-side when absent, so collector installs
that haven't upgraded yet keep working unchanged. Adding Gemini later is exactly one more `SOURCES`
entry and one more `PROVIDER_ADAPTERS` entry — no dispatch logic changes.

`backend/watcher.py` runs a second, independent watch loop directly inside the backend process
(not via the collector), hardcoded to `~/.claude/projects` — this only matters for a pure
local-single-machine dev setup (it's dead code in production, since Cloud Run has no `~/.claude`
or `~/.codex` directories). It stays Claude-Code-only; out of scope for this ticket.

## Codex adapter parsing logic (`backend/adapters/codex.py`)

Mirrors `claude_code.py`'s event-walking approach, adapted to Codex's JSONL format (verified
against real session files at `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` on this machine):

- **Run identity/timing:** `session_meta.payload.id` → `run_id`; `session_meta.payload.timestamp`
  → `started_at`; last event's timestamp → `ended_at` (when status is `"done"`, using the same
  mtime-based running/done heuristic as `claude_code.py`: `"running"` if the file was modified in
  the last 5 minutes, else `"done"`).
- **Model:** last-seen `turn_context.payload.model` (e.g. `"gpt-5-codex"`); defaults to
  `"gpt-5-codex"` if a session never has a `turn_context` event.
- **Tokens:** Codex's `token_count` events (`event_msg` type) carry a *cumulative* running total in
  `payload.info.total_token_usage.{input_tokens, output_tokens}` — confirmed against real data
  (`input_tokens: 3010, output_tokens: 128, total_tokens: 3138`, where `3010 + 128 = 3138` exactly,
  proving `total_tokens` is already the whole-session sum and `reasoning_output_tokens` is a
  subset of `output_tokens`, not additional to it). Unlike `claude_code.py`, which sums per-message
  deltas across every message, this adapter takes the **last** non-null `token_count` event's
  values directly — summing here would wildly over-count, since each successive event already
  includes everything counted before it. Both approaches compute the same thing (the true
  session-total tokens); they differ only because the two source formats expose it differently.
- **Commits/PRs:** `function_call` events (`payload.name == "shell"`, the actual command in
  `payload.arguments` as a JSON string: `{"command": ["bash","-lc","<cmd>"], "workdir": "..."}`)
  are Codex's equivalent of Claude Code's `Bash` tool_use blocks. Matched by `call_id` to the
  corresponding `function_call_output` event (Codex's equivalent of `tool_result`; its `output`
  field is itself a JSON string containing `{"output": "<stdout text>", "metadata": {...}}`) to get
  the real command output, then run through the shared `COMMIT_HASH_RE`/`PR_URL_RE`/
  `GITHUB_REPO_RE` — same pending-call-id-set pattern as `claude_code.py`, same "findall not
  search" reasoning (a single shell call can run `git commit` or `gh pr create` more than once).
- **First user message / label:** first `message`-type `response_item` event with `role: "user"`
  whose text doesn't start with `<` — Codex wraps injected context (`<environment_context>`,
  `<user_instructions>`) the same way Claude Code wraps its own meta text, so this reuses
  `claude_code.py`'s exact skip condition rather than inventing a new one.
- **Ticket refs:** same `_extract_tickets()` call (now shared, see below) over git branch name +
  first-user-text + label + all shell commands.

## Shared extraction module (`backend/adapters/_common.py`)

Everything in `claude_code.py` that's format-agnostic (operates on plain text, not on
Claude-specific event shapes) moves here: `TICKET_RE`, `_NON_TICKET_PREFIXES`, `GIT_COMMIT_RE`,
`GH_PR_RE`, `GIT_PUSH_RE`, `GIT_REMOTE_RE`, `COMMIT_HASH_RE`, `PR_URL_RE`, `GITHUB_REPO_RE`, and
the `_extract_tickets()` function. Both `claude_code.py` and the new `codex.py` import from it
instead of each defining their own copies — one place to fix bugs like AI-32's non-ticket-prefix
denylist fix, instead of two. `claude_code.py`'s own parsing logic (the event-walking loop,
Claude-specific field names) stays exactly where it is; only the shared regex/extraction layer
moves.

## Testing / error-handling plan

The backend currently has zero test coverage (matches the open AI-17 ticket) — this is the first
backend test file, self-contained and independent of AI-17's broader suite.

- **`backend/adapters/test_codex.py`**: unit tests for `parse_transcript_content` using real
  (trimmed/redacted) session content from this machine as fixtures — covering: cumulative token
  extraction (last `token_count` wins, not summed), commit/PR extraction via the
  `function_call`/`function_call_output` call-id pairing, ticket-ref extraction from
  branch/label/commands, and the environment-context-skip for first-user-text (so
  `<environment_context>` never becomes the task label).
- **`backend/adapters/test_common.py`**: tests confirm both adapters get identical extraction
  behavior from the shared module (e.g. the AI-32 non-ticket-prefix denylist applies to Codex
  sessions too, not just Claude Code).
- **Collector**: unit test that each `SOURCES` entry independently no-ops when its directory
  doesn't exist (matching today's single-source behavior), and that the shipped `X-Provider`
  header matches the source key.
- **Backend dispatch**: test that `/v1/ingest` routes to the right adapter based on `X-Provider`,
  and defaults to `claude_code` when the header is absent (old-collector compatibility).
- **Live verification**: once implemented, ingest a real Codex session from this machine
  end-to-end and confirm it shows up on the dashboard labeled `provider="openai"`.

## Decisions made during brainstorming

- Every Codex CLI session labeled `provider="openai"`, regardless of the actual backend model
  Codex routed to (matches the existing `claude_code.py` → `"anthropic"` precedent).
- Shared extraction regexes/helpers move into a new `backend/adapters/_common.py`, imported by
  both adapters, rather than being duplicated in the new one.
- Provider dispatch uses an explicit `X-Provider` header set by the collector (which already knows
  which source directory a file came from), not content-sniffing/auto-detection — designed as a
  registry (`SOURCES` / `PROVIDER_ADAPTERS`) so adding Gemini (AI-47) later is additive only.
- `backend/watcher.py`'s local-only watch loop stays Claude-Code-only — out of scope, since it's
  dead code in production.

## Out of scope

- Gemini adapter (AI-47, separate ticket) — this design only ensures the pipeline generalizes
  cleanly to it later.
- Updating `backend/watcher.py`'s local-dev-only watch loop for Codex.
- Detecting/special-casing which actual backend model a Codex CLI session routed to (local Ollama,
  OpenRouter, etc.) — always labeled `provider="openai"`.
- AI-17's broader backend pytest suite — this ticket's tests are self-contained additions, not a
  dependency on that ticket landing first.
