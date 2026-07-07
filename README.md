# ai-dash

Observability dashboard for AI coding agents. Tracks runs, token usage, commits, and PRs across Claude Code, OpenAI, and Gemini in a single read-only UI.

**Live:** https://dash.ai-coordinator.io

---

## Architecture

```
~/.claude/projects/*.jsonl
        │
        │  collector daemon (watches + ships)
        ▼
POST /api/v1/ingest  (X-API-Key auth, unaffected by auth gate below)
        │
        ▼
  Cloud Run (FastAPI + React)
        │  ── auth gate: DASHBOARD_PASSWORD Basic Auth (fallback)
        │     or per-user session cookie (see Auth section) ──
        ▼
  Cloud SQL (Postgres)
```

The **collector** runs locally, watches your Claude Code transcript files, and ships them to the central server. The server parses them into a unified `AgentRun` schema and serves the dashboard, gated by per-user login (see [Auth](#auth)).

---

## Stack

| Layer | Choice |
|---|---|
| Frontend | Vite + React 18 + TypeScript + Tailwind CSS |
| Charts | Recharts |
| Data fetching | TanStack Query (5s refetch + SSE) |
| Backend | FastAPI + Python 3.12 |
| DB | PostgreSQL via SQLModel (Cloud SQL on GCP) |
| Real-time | Server-Sent Events |
| Infra | Terraform → GCP (Cloud Run + Cloud SQL + Artifact Registry + Secret Manager) |
| DNS / proxy | Cloudflare (Worker proxies `dash.ai-coordinator.io` → Cloud Run) |

---

## Local development

```bash
# Postgres (matches production; run once, keeps running in the background)
docker compose up -d
cp .env.example .env

# Backend
python -m venv .venv && source .venv/bin/activate
pip install -e .
uvicorn backend.main:app --reload

# Frontend (separate terminal)
cd frontend && npm install && npm run dev
```

Without the `docker compose up -d` + `.env` step, the backend falls back to a local
`sqlite:///./ai_dash.db` file, which is fine for a quick look, but it can drift from Postgres-only
behavior, so prefer the Postgres setup above for anything beyond a quick check.

Frontend: http://localhost:5173  
Backend API: http://localhost:8000

---

## Collector setup

The collector watches `~/.claude/projects/` and ships transcripts to the server. Get an API key
from an admin (Settings → API Keys on the dashboard) before you start.

**One-line install (recommended):**
```bash
curl -fsSL https://dash.ai-coordinator.io/install.sh | bash
```

This creates a dedicated virtualenv (isolated from any other Python project on your machine),
downloads the collector, prompts for your API key on first run only, and registers it as a
launchd (macOS) / systemd (Linux) service that restarts automatically and logs to
`~/.ai_dash/collector.log` (rotated at 5MB × 3 backups, ~20MB max). Re-running the command is
safe: it reuses the existing virtualenv and config, and just refreshes the collector code and
service definition.

**Manual run** (advanced: foreground only, no background service). Requires creating the config
file yourself first, since this path has no interactive prompt:
```bash
mkdir -p ~/.ai_dash && cat > ~/.ai_dash/config.json <<'EOF'
{"url": "https://dash.ai-coordinator.io", "key": "your-api-key"}
EOF
python -m collector.collector
```
Dependencies (`httpx`, `watchfiles`) install automatically on first run.

---

## GCP deployment

Infrastructure is in `infra/` (Terraform). Requires a `infra/terraform.tfvars` (gitignored):

```hcl
project_id         = "devops-ai-tools"
region             = "us-central1"
db_password        = "..."
dashboard_password = "..."
session_secret     = "..."  # signs per-user login session cookies; pick a long random value
```

```bash
cd infra
terraform init
terraform apply
```

After apply, build and push the image:

```bash
docker buildx build --platform linux/amd64 \
  -t us-central1-docker.pkg.dev/devops-ai-tools/ai-dash/app:latest \
  --push .
```

---

## Auth

New deployments start password-protected: the dashboard is gated by HTTP Basic Auth, using the
`DASHBOARD_PASSWORD` env var (stored in GCP Secret Manager, set via `terraform.tfvars`). Username
is ignored; only the password is checked.

As soon as the first user account is created (Settings → Users), Basic Auth is retired for that
deployment and only per-user login (`/login`, session cookie signed with `SESSION_SECRET`, 30-day
expiry) works from then on. This is a one-way cutover: anyone who created that first account will
need to log in with it explicitly; their browser's cached Basic Auth credentials stop working on
the very next request.

Non-admin users only see their own runs. Admins see everyone's runs and can create/revoke
accounts and API keys from Settings.

API ingest requires an `X-API-Key` header. Keys are seeded in the DB on first startup
(`adk_devkey_local` for local dev) and are managed from Settings by admins.

---

## Domain

`dash.ai-coordinator.io` is routed via a Cloudflare Worker that rewrites the `Host` header before proxying to the Cloud Run service URL. DNS is managed in Cloudflare; domain is registered at Squarespace.
