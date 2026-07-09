# Agents Observability Dashboard: Plan

## Context

Build a read-only observability dashboard for AI agents across three providers: Claude Code (Anthropic), OpenAI, and Gemini. The dashboard surfaces run history, activity timelines (ticket → task → commits → PRs), trace trees, and token usage in a fast React UI. No control-plane features for now: pure observability.

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
  │  (own X-API-Key auth, unaffected by the auth gate below)   │
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
  │  ── AUTH GATE (everything below) ───────────────────────    │
  │  0 accounts exist → DASHBOARD_PASSWORD Basic Auth (fallback) │
  │  ≥1 account exists → session cookie only (Basic Auth retired,│
  │                       one-way cutover, see AI-7)            │
  │  POST /login /logout   GET /me                               │
  │  GET/POST/DELETE/PATCH /accounts  (admin-managed users)       │
  │                            │                               │
  │  GET /api/runs  /api/stats  /api/daily  /api/providers      │
  │    (scoped: non-admin sees own runs only, admin sees all;    │
  │     /api/stats includes estimated $ cost, see AI-5)          │
  │  SSE /api/stream  (live push, same per-user scoping)          │
  │  DELETE /api/runs  (admin-only, batch + cascade + dry_run)    │
  │  GET/POST/DELETE /keys  (API keys, admin-only)               │
  └──────────────────────────┬──────────────────────────────────┘
                             │  REST + SSE (session cookie)
                             ▼
  ┌─────────────────────────────────────────────────────────────┐
  │        REACT FRONTEND (served from same Cloud Run)          │
  │                                                             │
  │  /login         Login form → sets session cookie             │
  │  /              Overview: cards, charts, provider breakdown │
  │  /runs          All runs table (admin can filter by user)   │
  │  /runs/:id      Run detail: timeline, trace tree, tokens    │
  │  /settings      Users (accounts) + API keys (admin-only)     │
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
| Frontend | Vite + React 19 + TypeScript | Fast, lightweight, no SSR overhead needed |
| Styling | Tailwind CSS | Utility-first, pairs well with component libs |
| Data fetching | TanStack Query | Caching, background refresh, SSE integration |
| Charts | Recharts | Composable, React-native, small bundle |
| Tables | Plain paginated table | Offset pagination is enough at current run volumes |
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
    metadata: dict                  # provider-specific extras, incl. cached_input_tokens (see below)
    estimated_input_cost_usd: float | None   # see Cost tracking below
    estimated_output_cost_usd: float | None
    estimated_cost_usd: float | None
```

### Token accounting

`input_tokens` excludes cached (prompt-cache-read) tokens for all three providers; each provider's
adapter subtracts the cached portion from what the transcript reports, since that portion is billed
at a discount rather than full price and would otherwise inflate a long session's apparent cost.
`meta.cached_input_tokens` captures that excluded portion separately.

### Cost tracking (AI-5)

`backend/pricing.py` maps `(provider, model)` to a hardcoded $/1M-token price via case-insensitive
tier-keyword substring matching (e.g. any Anthropic model string containing `"sonnet"`), so pricing
survives new dated model releases without a code change. Computed once at ingest (`_upsert()`) and
recomputed on every update; a one-time startup backfill computes it for historical rows too. A run
whose model doesn't match any known tier gets `null` for all three cost fields rather than a guessed
price. Cache-read and cache-write (`cache_creation_input_tokens`) tokens are priced too, at their
respective discount/premium rates, not just fresh input tokens.

### Activity Timeline (Claude Code via MCP)
Claude Code streams events to the MCP server during each session. The MCP adapter extracts:
- Initial human prompt → `task_description`
- Tool calls containing `git commit` → commit hash → `git_commits`
- Tool calls containing `gh pr create` → PR URL → `git_prs`
- Git branch name, commit messages, prompt → regex → `ticket_refs`
  - Patterns: `LINEAR-\d+`, `[A-Z]+-\d+` (Jira), `#\d+` (GitHub Issues)
  - Optional: resolve to URLs if user configures their ticket system in settings
- `user` resolved from the API key used to connect; no need to read `$USER`

---

## Server-Side Adapters

1. **Claude Code adapter** (`backend/adapters/claude_code.py`): **built**
   - Parses Claude Code JSONL transcript files
   - Extracts tool calls, token usage, git commits/PRs, ticket refs → `AgentRun`
   - Called by the ingest endpoint when collector ships a transcript

2. **OpenAI/Codex CLI adapter** (`backend/adapters/codex.py`): **built**
   - Parses Codex CLI JSONL transcripts into the same unified `AgentRun` shape
   - Called by the ingest endpoint when `X-Provider: openai` is set

3. **Gemini CLI adapter** (`backend/adapters/gemini_cli.py`): **built**
   - Parses Gemini CLI transcripts into the same unified `AgentRun` shape
   - Called by the ingest endpoint when `X-Provider: gemini` is set

> **Out of scope for v1**: MCP server, transparent proxy adapters, desktop apps, mobile apps.

---

## Backend Routes

```
GET    /api/runs            # paginated list, filterable by provider/status/user/ticket/date (scoped per-user)
GET    /api/runs/:id        # single run detail (scoped per-user)
DELETE /api/runs            # batch delete by id, with cascade to sub-agent children,
                            #   dry_run mode, and audit logging (admin-only, max 100 ids/request)
GET    /api/stats           # 7-day summary (runs, tokens, commits, PRs, estimated $ cost, by provider), scoped per-user
GET    /api/daily           # per-day breakdown for charts (scoped per-user)
GET    /api/providers       # active providers
GET    /api/users           # users seen in runs (scoped per-user)
GET    /api/stream          # SSE stream of live run events (scoped per-user)
POST   /api/v1/ingest       # collector ships raw JSONL transcript here (X-API-Key auth, unchanged)
GET    /collector.py        # collector script download
GET    /install.sh          # one-command install script

POST   /api/login           # username + password → sets signed session cookie (30d expiry)
POST   /api/logout          # clears session cookie
GET    /api/me              # current session identity ({username, is_admin}); null username = no accounts yet
GET    /api/accounts        # list user accounts (admin-only)
POST   /api/accounts        # create account (open when zero accounts exist: bootstrap; admin-only after)
DELETE /api/accounts/:username  # revoke account (admin-only, blocks removing the last admin)
PATCH  /api/accounts/:username  # toggle is_admin (admin-only, blocks demoting the last admin)
GET    /api/keys            # list API keys (admin-only)
POST   /api/keys            # create API key (admin-only)
DELETE /api/keys/:key_prefix    # revoke API key (admin-only)
```

---

## Frontend Pages

### `/login`: Login
- Username + password form, posts to `/api/login`, redirects to `/` on success
- Rendered outside the main `<Layout>` (no sidebar/nav)

### `/`: Overview Dashboard
- Time range selector: 24h / 7d / 30d / 90d / All
- Summary cards: total runs, total tokens, commits, PRs opened, estimated $ spend (see AI-5)
- Live indicator badge when runs are currently in progress
- Charts: runs-per-day and token burn-per-day, stacked by provider
- Provider breakdown: runs/tokens/commits per provider

### `/runs`: All Runs Table
- Offset-based pagination (50 rows/page)
- Filter by: provider, status, user (admin-only filter), ticket
- Columns: task, provider, model, user, status, duration, tokens, ticket, code (merged PR/commit links)

### `/runs/:id`: Run Detail
- Header: user, model, duration, status badge, ticket chip(s) (linked to ticket system)
- Activity timeline: ticket → task → tool calls → git commits (linked) → PRs opened (linked)
- Trace tree: nested expand/collapse for sub-agent calls (via `parent_id`)
- Token breakdown: input vs output, per-message if available
- Raw metadata drawer (collapsible)

### `/settings`: Settings
- Users section: create/revoke accounts, toggle admin (admin-only; shows a create-first-account form instead when zero accounts exist yet)
- API Keys section: create/revoke ingest API keys (admin-only)
- Non-admins see neither section

---

## File Structure

```
ai_dash/
├── frontend/
│   ├── src/
│   │   ├── components/      # Card, Badge, Sparkline, TraceTree, RunsTable, Layout
│   │   ├── pages/           # Dashboard, Runs, RunDetail, Settings, Login
│   │   ├── lib/
│   │   │   ├── api.ts       # TanStack Query hooks (incl. useMe/login/logout)
│   │   │   └── sse.ts       # SSE client hook
│   │   └── main.tsx
│   ├── index.html
│   ├── vite.config.ts
│   └── package.json
├── backend/
│   ├── adapters/
│   │   ├── claude_code.py   # Claude Code JSONL transcript parser
│   │   ├── codex.py         # Codex CLI (OpenAI) transcript parser
│   │   └── gemini_cli.py    # Gemini CLI transcript parser
│   ├── api/
│   │   ├── routes.py        # runs/stats/daily/providers/users/keys/ingest/stream
│   │   └── auth_routes.py   # login/logout/me/accounts CRUD
│   ├── auth.py               # password hashing, session tokens, auth dependencies
│   ├── pricing.py            # model pricing table + cost estimation (AI-5)
│   ├── models.py
│   ├── db.py
│   └── main.py
├── cloudbuild.yaml          # GCP Cloud Build (build + deploy to Cloud Run)
└── pyproject.toml
```

---

## Build Order

1. ✅ Backend foundation: FastAPI app, Postgres schema, `/api/runs` with seed data
2. ✅ Frontend shell: Vite + Tailwind + routing + layout
3. ✅ Claude Code adapter: parse JSONL transcripts → AgentRun
4. ✅ Dashboard + Runs pages: wired to real data
5. ✅ SSE live updates: ingest → SSE push → frontend refresh
6. ✅ GCP deploy: Terraform → Cloud Run + Cloud SQL + Artifact Registry + Secret Manager
7. ✅ Custom domain: `dash.ai-coordinator.io` via Cloudflare Worker
8. ✅ Collector daemon: ships local transcripts to live server
9. ✅ Trace tree: `parent_id` linkage + nested expand/collapse UI in RunDetail
10. ✅ OpenAI adapter: Codex CLI transcript parser, same shape as Claude Code (AI-46)
11. ✅ Gemini adapter: Gemini CLI transcript parser, same shape as Claude Code (AI-47)
12. ✅ Multi-user data model: `user` field on `AgentRun`/`ApiKey`; initial client-side filter dropdown (later replaced by real per-user auth in AI-7)
13. ✅ API key management UI: Settings page with create/copy/delete (now admin-only, see AI-7)
14. ✅ Cost tracking: estimated $ spend from token counts × model pricing table (incl. cache read/write pricing), shown on dashboard + run detail (AI-5)
15. ✅ Installer: `install.sh` one-liner; launchd/systemd service setup (AI-6)
16. ⬜ Clickable links: PR URLs, GitHub commit links, and ticket refs (Linear / Jira) in run list and detail; prefer PR over bare commit; fall back to commit URL constructed from git remote; ticket URL from configured org base URL in Settings (AI-8)
17. ✅ Auth: per-user accounts, session-cookie login, and per-user data scoping, replacing the shared dashboard password (AI-7). `DASHBOARD_PASSWORD` remains as a one-way fallback until the first account is created.
18. ✅ Remove seed/demo data from production DB (AI-9)
19. ⬜ Backend pytest test suite for API + ingest logic (AI-17)
20. ✅ CI: automatic deploy to Cloud Run on merge to main (AI-18)

---

## Out of Scope (v1)

- All desktop apps (Claude, Gemini)
- Mobile apps
- Control plane (stop/retry agents)
