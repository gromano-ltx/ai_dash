# Agents Observability Dashboard — Plan

## Context

Build a read-only observability dashboard for AI agents across three providers: Claude Code (Anthropic), OpenAI, and Gemini. The dashboard surfaces run history, activity timelines (task → commits → PRs), trace trees, and token usage in a fast React UI. No control-plane features for now — pure observability.

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                        DATA SOURCES                         │
│                                                             │
│  Claude Code CLI          OpenAI (Codex CLI / API)          │
│  ~/.claude/*/agent-*.jsonl   OPENAI_BASE_URL=:8001          │
│         │                        │                          │
│  Gemini API                                                 │
│  GOOGLE_API_ENDPOINT=:8002        │                         │
│         │                        │                          │
└─────────┼────────────────────────┼──────────────────────────┘
          │                        │
          ▼                        ▼
┌─────────────────────────────────────────────────────────────┐
│                     FASTAPI BACKEND                         │
│                                                             │
│  ┌──────────────┐  ┌─────────────────┐  ┌───────────────┐  │
│  │  CC Adapter  │  │  OpenAI Adapter │  │ Gemini Adapter│  │
│  │ (file watch) │  │  (local proxy)  │  │ (local proxy) │  │
│  └──────┬───────┘  └────────┬────────┘  └───────┬───────┘  │
│         └──────────────────┼───────────────────┘           │
│                            ▼                               │
│                   ┌─────────────────┐                      │
│                   │  Unified Schema │                      │
│                   │    AgentRun     │                      │
│                   └────────┬────────┘                      │
│                            ▼                               │
│                      ┌──────────┐                          │
│                      │  SQLite  │                          │
│                      └──────────┘                          │
│                                                             │
│  REST API  /api/runs  /api/runs/:id  /api/providers         │
│  SSE       /api/stream  (live push on new runs)             │
└──────────────────────────┬──────────────────────────────────┘
                           │  REST + SSE
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                   REACT FRONTEND (Vite)                     │
│                                                             │
│  /              Overview — cards, sparklines, recent runs   │
│  /runs          All runs table — filter by user/provider    │
│  /runs/:id      Run detail — timeline, trace tree, tokens   │
└─────────────────────────────────────────────────────────────┘
```

---

## Tech Stack

| Layer | Choice | Reason |
|---|---|---|
| Frontend | Vite + React 18 + TypeScript | Fast, lightweight, no SSR overhead needed |
| Styling | Tailwind CSS | Utility-first, pairs well with component libs |
| Data fetching | TanStack Query | Caching, background refresh, SSE integration |
| Charts | Recharts | Composable, React-native, small bundle |
| Tables | TanStack Table | Headless, high-perf virtual rows for large run lists |
| Backend | FastAPI + Python 3.12 | Native AI SDK support, great SSE/async |
| DB | SQLite via SQLModel | Zero-ops, portable, sufficient for local/small-team use |
| Real-time | Server-Sent Events (SSE) | One-way stream from backend → frontend for live runs |

---

## Unified Data Model

```python
class AgentRun:
    id: str
    provider: Literal["anthropic", "openai", "gemini"]
    model: str
    status: Literal["running", "done", "failed"]
    started_at: datetime
    ended_at: datetime | None       # derived: duration = ended_at - started_at
    input_tokens: int
    output_tokens: int
    label: str                      # first ~80 chars of prompt
    task_description: str | None    # fuller task context / initial human turn
    user: str | None                # human who triggered the run (git config / $USER)
    git_commits: list[str]          # commit hashes made during this run
    git_prs: list[str]              # PR URLs opened during this run
    parent_id: str | None           # for nested trace trees
    metadata: dict                  # provider-specific extras
```

> **Cost omitted for v1** — no provider API reliably exposes billing data at the run level. Will be added later.

### Activity Timeline (Claude Code)
The JSONL transcript logs every tool call. The Claude Code adapter mines these for:
- Initial human prompt → `task_description`
- `Bash` calls containing `git commit` → extract commit hash → `git_commits`
- `Bash` calls containing `gh pr create` → extract PR URL → `git_prs`
- Session username from `git config user.name` or `$USER` → `user`

---

## Provider Adapters

1. **Claude Code** (`backend/adapters/claude_code.py`)
   - Reads `~/.claude/projects/*/transcripts/agent-*.jsonl` (CLI only)
   - File-watcher (watchdog) triggers re-ingest on new/updated files
   - Mines tool calls for git activity and task description

2. **OpenAI / Codex CLI** (`backend/adapters/openai.py`)
   - Local proxy on `localhost:8001`; user sets `OPENAI_BASE_URL=http://localhost:8001` once
   - Intercepts requests/responses, extracts model + tokens + prompt → `AgentRun`
   - Note: if Codex CLI writes local session files, a dedicated file-based adapter may be added

3. **Gemini** (`backend/adapters/gemini.py`)
   - Local proxy on `localhost:8002`; user sets `GOOGLE_API_ENDPOINT=http://localhost:8002`
   - Maps `GenerateContent` requests/responses to `AgentRun`

> **Out of scope for v1**: All desktop apps (Claude, Gemini), mobile apps — deferred to v2.

---

## Backend Routes

```
GET  /api/runs              # paginated list, filterable by provider/status/user/date
GET  /api/runs/:id          # single run detail + trace children
GET  /api/runs/:id/trace    # full nested trace tree
GET  /api/providers         # which providers are configured
GET  /api/stream            # SSE stream of live run events
POST /api/ingest/:provider  # manual trigger to re-ingest (for dev)
```

---

## Frontend Pages

### `/` — Overview Dashboard
- Summary cards: total runs (7d), total tokens (7d), active providers, commits/PRs made
- Sparkline: runs-per-day per provider
- Recent runs list (last 10)

### `/runs` — All Runs Table
- TanStack Table with virtual rows (handles thousands of runs)
- Filter by: provider, model, status, user, date range
- Columns: label, provider, model, user, status, duration, tokens, commits, PRs, started_at

### `/runs/:id` — Run Detail
- Header: user, model, duration, status badge
- Activity timeline: task → tool calls → git commits (linked) → PRs opened (linked)
- Trace tree: nested expand/collapse for sub-agent calls (via `parent_id`)
- Token breakdown: input vs output, per-message if available
- Raw metadata drawer (collapsible)

---

## File Structure

```
ai_dash/
├── frontend/
│   ├── src/
│   │   ├── components/      # Card, Badge, Sparkline, TraceTree, RunsTable
│   │   ├── pages/           # Dashboard, Runs, RunDetail
│   │   ├── lib/
│   │   │   ├── api.ts       # TanStack Query hooks
│   │   │   └── sse.ts       # SSE client hook
│   │   └── main.tsx
│   ├── index.html
│   ├── vite.config.ts
│   └── package.json
├── backend/
│   ├── adapters/
│   │   ├── claude_code.py
│   │   ├── openai.py
│   │   └── gemini.py
│   ├── api/
│   │   └── routes.py
│   ├── models.py
│   ├── db.py
│   └── main.py
└── pyproject.toml
```

---

## Build Order

1. Backend foundation — FastAPI app, SQLite schema, `/api/runs` stub with mock data
2. Frontend shell — Vite setup, Tailwind, routing, layout, connect to mock API
3. Claude Code adapter — parse JSONL transcripts → real data in DB
4. Dashboard + Runs pages — wire real data end-to-end
5. Trace tree — `parent_id` linkage + nested UI component
6. OpenAI adapter — add second provider (+ Codex CLI discovery)
7. Gemini adapter — add third provider
8. SSE live updates — file-watcher → SSE stream → frontend badge refresh

---

## Out of Scope (v1)

- Cost / billing data
- All desktop apps (Claude, Gemini)
- Mobile apps
- Control plane (stop/retry agents)
