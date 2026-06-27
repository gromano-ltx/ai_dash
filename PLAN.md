# Agents Observability Dashboard — Plan

## Context

Build a read-only observability dashboard for AI agents across three providers: Claude Code (Anthropic), OpenAI, and Gemini. The dashboard surfaces run history, activity timelines (ticket → task → commits → PRs), trace trees, and token usage in a fast React UI. No control-plane features for now — pure observability.

**Design principle**: zero local installation on team members' machines. Data flows to the central server via a remote MCP server (Claude Code) and a network proxy (OpenAI/Gemini). Each team member sets up in under 2 minutes with a config edit and one env var.

---

## Architecture Diagram

```
  Team member machines (zero daemons, zero installers)
  ┌──────────────────────────────────────────────────┐
  │                                                  │
  │  Claude Code CLI                                 │
  │  ~/.claude/settings.json → MCP server URL        │
  │  (CC streams events to remote MCP during session)│
  │                          │                       │
  │  OpenAI / Codex CLI      │                       │
  │  OPENAI_BASE_URL=:8001   │                       │
  │                │         │                       │
  │  Gemini CLI    │         │                       │
  │  GOOGLE_API_ENDPOINT=:8002                       │
  │                │         │                       │
  └────────────────┼─────────┼───────────────────────┘
                   │         │
                   ▼         ▼
  ┌─────────────────────────────────────────────────────────────┐
  │                   CENTRAL SERVER                            │
  │                                                             │
  │  ┌──────────────┐  ┌─────────────────┐  ┌───────────────┐  │
  │  │  MCP Server  │  │  OpenAI Proxy   │  │ Gemini Proxy  │  │
  │  │  /mcp        │  │  :8001          │  │ :8002         │  │
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

## Team Onboarding (per member)

**Claude Code** — edit `~/.claude/settings.json`:
```json
{
  "mcpServers": {
    "ai-dash": {
      "url": "http://your-server/mcp",
      "apiKey": "user-api-key"
    }
  }
}
```

**OpenAI / Codex CLI** — add to shell profile:
```bash
export OPENAI_BASE_URL=http://your-server:8001
```

**Gemini** — add to shell profile:
```bash
export GOOGLE_API_ENDPOINT=http://your-server:8002
```

Total: one config edit + one or two env vars. No daemon, no installer, no Docker on their machine.

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
    user: str | None                # resolved from API key identity
    git_commits: list[str]          # commit hashes made during this run
    git_prs: list[str]              # PR URLs opened during this run
    ticket_refs: list[str]          # e.g. ["LINEAR-123", "#456", "PROJ-789"]
    parent_id: str | None           # for nested trace trees
    metadata: dict                  # provider-specific extras
```

> **Cost omitted for v1** — no provider API reliably exposes billing data at the run level. Will be added later.

### Activity Timeline (Claude Code via MCP)
Claude Code streams events to the MCP server during each session. The MCP adapter extracts:
- Initial human prompt → `task_description`
- Tool calls containing `git commit` → commit hash → `git_commits`
- Tool calls containing `gh pr create` → PR URL → `git_prs`
- Git branch name, commit messages, prompt → regex → `ticket_refs`
  - Patterns: `LINEAR-\d+`, `[A-Z]+-\d+` (Jira), `#\d+` (GitHub Issues)
  - Optional: resolve to URLs if user configures their ticket system in settings
- `user` resolved from the API key used to connect — no need to read `$USER`

---

## Server-Side Adapters

1. **Claude Code MCP adapter** (`backend/adapters/claude_code.py`)
   - Implements the MCP server protocol at `/mcp`
   - Receives streaming events from Claude Code sessions over HTTP
   - Extracts tool calls, token usage, git activity, ticket refs → `AgentRun`
   - Replaces the file-watcher approach entirely — no local file access needed

2. **OpenAI / Codex CLI proxy** (`backend/adapters/openai.py`)
   - Transparent HTTPS proxy on `:8001`
   - Intercepts requests/responses, extracts model + tokens + prompt → `AgentRun`
   - Forwards request to real OpenAI API unmodified

3. **Gemini proxy** (`backend/adapters/gemini.py`)
   - Transparent HTTPS proxy on `:8002`
   - Maps `GenerateContent` requests/responses to `AgentRun`

> **Out of scope for v1**: All desktop apps (Claude, Gemini), mobile apps — deferred to v2.

---

## Backend Routes

```
GET  /api/runs              # paginated list, filterable by provider/status/user/ticket/date
GET  /api/runs/:id          # single run detail + trace children
GET  /api/runs/:id/trace    # full nested trace tree
GET  /api/providers         # which providers are configured
GET  /api/stream            # SSE stream of live run events
POST /mcp                   # MCP server endpoint (Claude Code connects here)
```

---

## Frontend Pages

### `/` — Overview Dashboard
- Summary cards: total runs (7d), total tokens (7d), active providers, commits/PRs made
- Sparkline: runs-per-day per provider
- Recent runs list (last 10)

### `/runs` — All Runs Table
- TanStack Table with virtual rows (handles thousands of runs)
- Filter by: provider, model, status, user, ticket, date range
- Columns: label, provider, model, user, status, duration, tokens, ticket, commits, PRs, started_at

### `/runs/:id` — Run Detail
- Header: user, model, duration, status badge, ticket chip(s) (linked to ticket system)
- Activity timeline: ticket → task → tool calls → git commits (linked) → PRs opened (linked)
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
│   │   ├── claude_code.py   # MCP server + event parser
│   │   ├── openai.py        # HTTPS proxy :8001
│   │   └── gemini.py        # HTTPS proxy :8002
│   ├── api/
│   │   └── routes.py
│   ├── models.py
│   ├── db.py
│   └── main.py
├── docker-compose.yml       # single command to deploy the server
└── pyproject.toml
```

---

## Build Order

1. Backend foundation — FastAPI app, SQLite schema, `/api/runs` stub with mock data
2. Frontend shell — Vite setup, Tailwind, routing, layout, connect to mock API
3. MCP server adapter — receive CC events, parse into AgentRun, persist to DB
4. Dashboard + Runs pages — wire real data end-to-end
5. Trace tree — `parent_id` linkage + nested UI component
6. OpenAI proxy adapter — add second provider
7. Gemini proxy adapter — add third provider
8. SSE live updates — new AgentRun inserted → SSE push → frontend refresh
9. Docker Compose — package everything for one-command server deploy

---

## Out of Scope (v1)

- Cost / billing data
- All desktop apps (Claude, Gemini)
- Mobile apps
- Control plane (stop/retry agents)
