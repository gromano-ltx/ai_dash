# AI-47: Gemini CLI transcript ingestion adapter

## Context

The README and dashboard both advertise support for "Claude Code, OpenAI, and Gemini"
(`backend/api/routes.py`'s `PROVIDERS = ("anthropic", "openai", "gemini")` is already wired into
every aggregate/bucket endpoint), but `backend/adapters/` only contains `claude_code.py` and
`codex.py` (added in AI-46). No Gemini coding-agent session has ever been ingested: the "gemini"
bucket in every dashboard chart is permanently zero.

AI-46 already generalized the collector/backend pipeline (a `SOURCES` dict in the collector, a
`PROVIDER_ADAPTERS` dict in the backend, shared extraction helpers in `_common.py`) specifically so
a third provider could be added without further pipeline changes. This design adds that third
adapter, for **Gemini CLI** (confirmed installed at v0.49.0 on this machine, with real local
session transcripts under `~/.gemini/tmp/*/chats/`).

Google's Antigravity (a separate agentic IDE, also rooted under `~/.gemini/`) was considered and
explicitly ruled out for this ticket: different tool, untested transcript format, would need its
own investigation.

## Architecture & dispatch

No pipeline changes are required beyond one entry in each existing registry:

```
Collector (collector/collector.py)
   SOURCES = {
     "anthropic": ~/.claude/projects,
     "openai":    ~/.codex/sessions,
     "gemini":    ~/.gemini/tmp,        # ← added, this ticket
   }

   POST /api/v1/ingest  (backend/api/routes.py)
   PROVIDER_ADAPTERS = {
     "anthropic": claude_code.parse_transcript_content,
     "openai":    codex.parse_transcript_content,
     "gemini":    gemini_cli.parse_transcript_content,   # ← added, this ticket
   }
```

Gemini CLI's `~/.gemini/tmp/` is organized per-project (one subfolder per repo the CLI has been run
in, named by a human-readable slug or a hash), unlike Claude Code's/Codex's flatter per-provider
layout. This requires no changes to the collector's walk logic: `base.rglob("*.jsonl")` already
recurses through arbitrary subdirectory depth. Verified on this machine that all 16 `.jsonl` files
under `~/.gemini/tmp` live under a `chats/` directory: either `chats/session-*.jsonl` (top-level
sessions) or `chats/<parent-id>/<subagent-id>.jsonl` (subagent sessions, see below), so no filename
filtering is needed to exclude unrelated files.

## Gemini CLI transcript format (`backend/adapters/gemini_cli.py`)

Verified against real session files at `~/.gemini/tmp/<project>/chats/*.jsonl` on this machine. The
format is a hybrid checkpoint/event log, structurally different from both `claude_code.py`'s
(flat event stream) and `codex.py`'s (flat event stream) formats:

- **Header (line 1):** `sessionId`, `startTime`, `kind` (`"main"` or `"subagent"`), and for
  subagent sessions a `directories` list instead of a single cwd.
- **Most lines:** standalone events with a top-level `type` field: `"user"`, `"gemini"`, or
  `"info"`.
- **One early line:** `{"$set": {"messages": [...]}}`, wrapping the *first* real user message (the
  injected `<session_context>`/`<loaded_context>` text). The parser must unwrap this in addition to
  reading top-level `type` events; later `$set` lines carry only `lastUpdated`/`summary`/
  `memoryScratchpad` housekeeping and are ignored.
- **Verified duplication:** real sessions on this machine log the same message `id` twice in a row
  (a debounced-write artifact of the checkpoint format). The parser dedupes by `id` before
  accumulating tokens/text/tool calls, using a `seen_ids: set[str]` mirroring `claude_code.py`'s
  `seen_request_ids` pattern.

**Run identity/timing:** header `sessionId` → `run_id`/`session_id`; header `startTime` →
`started_at`. `ended_at` = last event's timestamp across any event type (mirrors `codex.py`'s
approach, since Gemini has no single "assistant-only" timestamp column the way Claude Code's
`last_assistant_ts` does), used only when `status == "done"` (same mtime-based 5-minute
running/done heuristic as the other two adapters).

**Model:** last-seen `model` field off `"gemini"`-type events (e.g. `"gemini-3.5-flash"`, the real
value observed locally); defaults to that same string if a session ends before any `"gemini"` event
fires (e.g. a session where the user immediately quit).

**Tokens:** each `"gemini"` event carries `tokens: {input, output, cached, thoughts, tool, total}`.
Verified against real data that `total = input + output + thoughts + tool` exactly, with `cached` a
subset of `input` (not additive), confirming `input` is each turn's own full-context prompt size
(growing every turn, but not a cumulative running counter the way Codex's `token_count` event is),
while `output`/`thoughts`/`tool` are each turn's fresh generation cost. Per sign-off: sum
`tokens.input` across all deduped `"gemini"` events → `input_tokens`; sum
`(tokens.output + tokens.thoughts + tokens.tool)` → `output_tokens`. This mirrors `claude_code.py`'s
summation approach (not `codex.py`'s last-value-wins), since each turn's value is genuine per-turn
usage rather than an already-cumulative total; thinking/tool tokens fold into `output_tokens` the
same way Codex's `reasoning_output_tokens` fold into its `output_tokens`.

**Commits/PRs:** each `"gemini"` event's `toolCalls` list carries entries like
`{id, name, args, result, status, timestamp}`. For `name == "run_shell_command"`: the command is a
plain string at `args.command` (unlike Codex's array-wrapped command, so no `' '.join()` needed), and
the output is at `result[0].functionResponse.response.output`. Unlike Claude Code/Codex, where a
tool call and its result live in two separate top-level events paired by `call_id` across a
pending-set, **Gemini's command and result already live together in the same object**, so
`_classify_shell_command` and `_resolve_command_output` (both reused unchanged from `_common.py`)
are called back-to-back in the same loop iteration, with no pending-id tracking required. Genuine
simplification enabled by this format.

**First user message / label:** first `"user"`-type event (including the one unwrapped from the
initial `$set.messages[0]`) whose text doesn't start with `<`, covering both `<session_context>`
(main sessions) and `<loaded_context>` (subagent sessions) injected-context wrapping, reusing the
same skip condition as the other two adapters. No `ai-title`-equivalent event exists in Gemini
transcripts, so the label is `first_user_text[:80]`, mirroring `codex.py` rather than
`claude_code.py`.

**Subagent linkage:** header `kind == "subagent"` → `agent_id` = that session's own `sessionId`,
`parent_id` = the parent folder name from the file path (`chats/<parent_id>/<subagent_id>.jsonl`).
Passed through `parse_transcript(path)` into `parse_transcript_content(..., parent_id=, agent_id=)`
exactly like `claude_code.py`'s subagent convention, so `run_id = f"agent-{agent_id}"` stays
consistent with how the dashboard already links parent/child runs.

**git_branch:** not available anywhere in Gemini CLI transcripts: no equivalent field exists.
`meta.git_branch` is always `None` for this adapter. Known gap; not required by this ticket's DoD.

**cwd:** for main sessions, read the sibling `.project_root` file one directory above `chats/`
(path-aware, done in `parse_transcript(path)`, not the content parser; this is the same separation
of concerns as `claude_code.py`'s subagent-path detection). For subagent sessions, take the first entry of the
header's `directories` list, falling back to `.project_root` if that list is empty.

**Ticket refs:** same shared `_extract_tickets()` call over
`[cwd, first_user_text, label] + bash_commands`, with `cwd` substituted for `git_branch` since branch
isn't available here.

## Testing / error-handling plan

Self-contained additions, independent of AI-17's broader pytest suite (same precedent as AI-46).

- **`backend/adapters/test_gemini_cli.py`**: unit tests using real (trimmed/redacted) fixture
  content from this machine, covering: dedup-by-`id` (using the verified real duplicate-line case),
  the token formula (`input` summed, `output+thoughts+tool` summed, verified against real
  `total = input+output+thoughts+tool` data), shell tool-call extraction via the combined
  command+result object shape (no pending-id state, unlike the other two adapters), subagent
  `parent_id`/`agent_id` extraction from the nested `chats/<parent>/<subagent>.jsonl` path,
  `.project_root` cwd lookup for main sessions with `directories[0]` fallback for subagents, and the
  first-user-text `<`-prefix skip for both `<session_context>` and `<loaded_context>` wrapping.
- **Collector**: unit test that `SOURCES["gemini"]` no-ops when `~/.gemini/tmp` doesn't exist, and
  that files under it ship with `X-Provider: gemini`.
- **Backend dispatch**: test that `/v1/ingest` routes to `gemini_cli.parse_transcript_content` for
  `X-Provider: gemini`.
- **Live verification**: once implemented, ingest a real local Gemini CLI session end-to-end
  (several exist on this machine already, including at least one that spawned a subagent) and
  confirm it shows up on the dashboard labeled `provider="gemini"` with correct tokens, and correct
  parent/child linkage in the trace tree for the subagent case.

## Decisions made during brainstorming

- Target Gemini CLI, not Antigravity (a separate Google agentic-IDE product also rooted under
  `~/.gemini/`), ruled out as untested/out of scope for this ticket.
- Token mapping: sum `tokens.input` → `input_tokens`; sum `(tokens.output + tokens.thoughts +
  tokens.tool)` → `output_tokens`, following `claude_code.py`'s summation precedent rather than
  `codex.py`'s last-value-wins (Gemini's per-turn token field is genuine per-turn usage, not an
  already-cumulative counter like Codex's).
- No filename filtering needed in the collector: verified all real `.jsonl` files on this machine
  live under `chats/`, matched cleanly by the existing recursive glob.
- Subagent detection mirrors `claude_code.py`'s parent/child convention, adapted to Gemini's
  `chats/<parent>/<subagent>.jsonl` nesting (vs Claude Code's `<parent>/subagents/agent-<id>.jsonl`).

## Out of scope

- Antigravity adapter (separate tool/format, explicitly ruled out this session).
- `git_branch` extraction for Gemini sessions (no equivalent field exists in the transcript format).
- `backend/watcher.py`'s local-only Claude-Code-specific watch loop (already carved out as
  dead-code-in-production by AI-46's precedent).
- AI-17's broader backend pytest suite dependency (this ticket's tests are self-contained additions).
