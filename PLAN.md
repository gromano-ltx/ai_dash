# Agents Observability Dashboard — Plan

## Context

Build a read-only observability dashboard for AI agents across three providers: Claude Code (Anthropic), OpenAI, and Gemini. The dashboard surfaces run history, activity timelines (ticket → task → commits → PRs), trace trees, and token usage in a fast React UI. No control-plane features for now — pure observability.

**Original design principle**: zero local installation on team members' machines via remote MCP server + network proxies.

**As built (v1)**: lightweight local collector daemon reads Claude Code JSONL transcripts and ships them via REST API. MCP/proxy approach deferred to v2.

---

## Architecture Diagram

### As built (v1)

```
  Developer machine
  ┌──────────────────────────────────────────────────┐
  │                                                  │
  │  Claude Code CLI                                 │
  │  writes ~/.claude/projects/*.jsonl               │
  │                          │                       │
  │  collector daemon        │                       │
  │  (~/.ai_dash/config.json)│                       │
  │  watches + ships via     │                       │
  │  POST /api/v1/ingest ────┘                       │
  │                                                  │
  └──────────────────────┬───────────────────────────┘
                         │  HTTPS + X-API-Key
                         ▼
  ┌─────────────────────────────────────────────────────────────┐
  │          CENTRAL SERVER (Cloud Run, GCP)                    │
  │                                                             │
  │  POST /api/v1/ingest  ← collector ships JSONL               │
  │                            │                               │
  │                   parse_transcript()                        │
  │                            ▼                               │
  │                   ┌─────────────────┐                      │
  │                   │    AgentRun     │                      │
  │                   └────────┬────────┘                      │
  │                            ▼                               │
  │                      ┌──────────┐                          │
  │                      │ Postgres │◄── Cloud SQL (GCP)       │
  │                      └──────────┘                          │
  │                                                             │
  │  GET /api/runs  /api/stats  /api/daily  /api/providers      │
  │  SSE /api/stream  (live push on new runs)                   │
  └──────────────────────────┬──────────────────────────────────┘
                             │  REST + SSE
                             ▼
  ┌─────────────────────────────────────────────────────────────┐
  │        REACT FRONTEND (served from same Cloud Run)          │
  │                                                             │
  │  /              Overview — cards, charts, provider breakdown │
  │  /runs          All runs table — filter by user/provider    │
  │  /runs/:id      Run detail — timeline, trace tree, tokens   │
  └─────────────────────────────────────────────────────────────┘

  DNS: dash.ai-coordinator.io → Cloudflare Worker → Cloud Run URL
```

### Original design (v2 target)

```
  Team member machines (zero daemons, zero installers)
  ┌──────────────────────────────────────────────────┐
  │  Claude Code CLI → MCP server URL (remote)       │
  │  OpenAI CLI      → OPENAI_BASE_URL=:8001         │
  │  Gemini CLI      → GOOGLE_API_ENDPOINT=:8002     │
  └──────────────────────────────────────────────────┘
```

---

## Collector Setup (current v1)

Create `~/.ai_dash/config.json`:
```json
{"url": "https://dash.ai-coordinator.io", "key": "your-api-key"}
```

Run the collector:
```bash
python -m collector.collector
```

Or install as a background service via `install.sh` (launchd on macOS, systemd on Linux).

The collector watches `~/.claude/projects/*.jsonl`, detects changes, and ships updated files to the server. State is tracked in `~/.ai_dash/state.json` to avoid re-shipping unchanged files.

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
| DB | PostgreSQL via SQLModel | Cloud SQL on GCP, smallest instance (~$10/mo), fully managed |
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

1. **Claude Code adapter** (`backend/adapters/claude_code.py`) — **built**
   - Parses Claude Code JSONL transcript files
   - Extracts tool calls, token usage, git commits/PRs, ticket refs → `AgentRun`
   - Called by the ingest endpoint when collector ships a transcript

2. **OpenAI proxy** (`backend/adapters/openai.py`) — **not yet built**
   - Planned: transparent HTTPS proxy on `:8001`

3. **Gemini proxy** (`backend/adapters/gemini.py`) — **not yet built**
   - Planned: transparent HTTPS proxy on `:8002`

> **Out of scope for v1**: MCP server, OpenAI/Gemini proxies, desktop apps, mobile apps.

---

## Backend Routes

```
GET  /api/runs              # paginated list, filterable by provider/status/user/ticket/date
GET  /api/runs/:id          # single run detail
GET  /api/stats             # 7-day summary (runs, tokens, commits, PRs, by provider)
GET  /api/daily             # per-day breakdown for charts
GET  /api/providers         # active providers
GET  /api/users             # users seen in runs
GET  /api/stream            # SSE stream of live run events
POST /api/v1/ingest         # collector ships raw JSONL transcript here
GET  /collector.py          # collector script download
GET  /install.sh            # one-command install script
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
├── cloudbuild.yaml          # GCP Cloud Build — build + deploy to Cloud Run
└── pyproject.toml
```

---

## Build Order

1. ✅ Backend foundation — FastAPI app, Postgres schema, `/api/runs` with seed data
2. ✅ Frontend shell — Vite + Tailwind + routing + layout
3. ✅ Claude Code adapter — parse JSONL transcripts → AgentRun
4. ✅ Dashboard + Runs pages — wired to real data
5. ✅ SSE live updates — ingest → SSE push → frontend refresh
6. ✅ GCP deploy — Terraform → Cloud Run + Cloud SQL + Artifact Registry + Secret Manager
7. ✅ Custom domain — `dash.ai-coordinator.io` via Cloudflare Worker
8. ✅ Collector daemon — ships local transcripts to live server
9. ⬜ Trace tree — `parent_id` linkage + nested expand/collapse UI
10. ⬜ OpenAI adapter — proxy or SDK integration
11. ⬜ Gemini adapter — proxy or SDK integration
12. ⬜ Multi-user isolation — per-user data visibility
13. ⬜ API key management UI — currently seeded manually

---

## Out of Scope (v1)

- Cost / billing data
- All desktop apps (Claude, Gemini)
- Mobile apps
- Control plane (stop/retry agents)
