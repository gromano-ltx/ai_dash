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
POST /api/v1/ingest
        │
        ▼
  Cloud Run (FastAPI + React)
        │
        ▼
  Cloud SQL (Postgres)
```

The **collector** runs locally, watches your Claude Code transcript files, and ships them to the central server. The server parses them into a unified `AgentRun` schema and serves the dashboard.

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
`sqlite:///./ai_dash.db` file — fine for a quick look, but it can drift from Postgres-only
behavior, so prefer the Postgres setup above for anything beyond a quick check.

Frontend: http://localhost:5173  
Backend API: http://localhost:8000

---

## Collector setup

The collector watches `~/.claude/projects/` and ships transcripts to the server.

**Config** — create `~/.ai_dash/config.json`:
```json
{"url": "https://dash.ai-coordinator.io", "key": "your-api-key"}
```

**Run manually:**
```bash
python -m collector.collector
```

**Run as a background service (recommended)** — the installer creates a dedicated virtualenv
(isolated from any other Python project on your machine), downloads the collector, and registers
it as a launchd (macOS) / systemd (Linux) service that restarts automatically and logs to
`~/.ai_dash/collector.log` (rotated at 5MB × 3 backups, ~20MB max):

```bash
curl -fsSL https://dash.ai-coordinator.io/install.sh | bash
```

Re-running the command is safe — it reuses the existing virtualenv and config, and just refreshes
the collector code and service definition.

---

## GCP deployment

Infrastructure is in `infra/` (Terraform). Requires a `infra/terraform.tfvars` (gitignored):

```hcl
project_id         = "devops-ai-tools"
region             = "us-central1"
db_password        = "..."
dashboard_password = "..."
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

The dashboard is protected by HTTP Basic Auth. Username is ignored; password is the `DASHBOARD_PASSWORD` env var (stored in GCP Secret Manager, set via `terraform.tfvars`).

API ingest requires an `X-API-Key` header. Keys are seeded in the DB on first startup (`adk_devkey_local` for local dev).

---

## Domain

`dash.ai-coordinator.io` is routed via a Cloudflare Worker that rewrites the `Host` header before proxying to the Cloud Run service URL. DNS is managed in Cloudflare; domain is registered at Squarespace.
