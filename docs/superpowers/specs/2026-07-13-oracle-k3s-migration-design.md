# Migrate ai_dash off GCP to Oracle Cloud + k3s

## Context

ai_dash currently runs entirely on GCP, owned by LTX's org/billing:

- **Cloud Run** — single container (FastAPI backend + built React SPA), 1 vCPU/512Mi.
- **Cloud SQL for Postgres 15** (`db-f1-micro`, private IP only).
- **Cloud Armor + external HTTPS LB** — WAF/rate-limiting (20 req/min/IP) in front of Cloud Run,
  mid-rollout on a two-phase ingress cutover (AI-41).
- **Secret Manager** (3 secrets: `DATABASE_URL`, `DASHBOARD_PASSWORD`, `SESSION_SECRET`).
- **Artifact Registry** (Docker images) + **Cloud Build**, triggered from GitHub Actions via
  Workload Identity Federation.
- Terraform (`infra/*.tf`) manages all of the above; state lives in a GCS bucket.

The user is leaving LTX and will lose access to this GCP org/billing account at the end of this
month (2026-07-31). The move is forced, not optional, and the replacement should cost as close to
$0/month as possible while still being reliable enough for daily use by a small team. ai_dash is
also being built toward eventual productization as a multi-user SaaS ([[ai-dash-direction]]), so
the infra choice should not be a dead end relative to that direction.

## Goals

- Be fully off GCP — traffic, data, and CI/CD — before GCP access is lost, with a safety margin
  (not cutting it to the exact last day).
- Run on infrastructure that costs $0/month baseline; a few $/month is acceptable if it buys real
  reliability (e.g. a second node, managed DB backups).
- Use real Kubernetes, not a Kubernetes-flavored abstraction, so the current investment carries
  forward if/when the product scales and a managed control plane (e.g. Oracle OKE) becomes
  worthwhile. No app-level rework should be required to make that later jump.
- Preserve today's functional behavior: HTTPS, a WAF/rate-limiting layer roughly equivalent to the
  current Cloud Armor policy, Postgres persistence, and automated deploy-on-push.

## Non-goals

- Multi-region redundancy, autoscaling to many nodes, or high-availability control plane — not
  needed at current (small team, daily use) scale.
- Migrating the collector daemon's logic or the `/api/v1/ingest` contract — it only needs a new
  DNS target.
- Standing up monitoring/alerting (Prometheus/Grafana) or `cert-manager`-based TLS automation as
  part of the initial cutover — deferred to post-deadline hardening.
- Codifying the new infra in Terraform on day one — deferred to post-deadline hardening (see
  Phased rollout).

## Target architecture

```
Client (browser / collector daemon)
   │  HTTPS (dashboard.<domain>)
   ▼
Cloudflare (free plan)
   - DNS + TLS termination (Full (strict), Cloudflare Origin CA cert on the origin)
   - WAF managed rules + a rate-limiting rule (~20 req/min/IP, mirrors current Cloud Armor policy)
   │  proxied HTTPS
   ▼
Oracle Cloud — Always Free Ampere A1 instance(s), reserved public IP
   └─ k3s (single-node to start; real, CNCF-conformant Kubernetes)
        ├─ Traefik (bundled with k3s) — ingress, holds the Cloudflare Origin CA cert as a TLS Secret
        ├─ Deployment: ai_dash app container (same multi-stage image as today)
        ├─ Service: ClusterIP → Traefik
        └─ Secrets: DATABASE_URL, SESSION_SECRET (applied out-of-band, not committed to git)
   │  Postgres wire protocol, over the public internet (TLS via Neon's `sslmode=require`)
   ▼
Neon (managed Postgres, free tier — 3GB, autosuspend, automatic backups)
```

Firewall rules on the Oracle instance mirror the existing AI-41 pattern: initially allow inbound
80/443 from anywhere to validate the stack, then (once Cloudflare is confirmed as the only path in)
restrict the OCI security list / NSG to Cloudflare's published IP ranges only, so the WAF/rate-limit
layer can't be bypassed by hitting the origin IP directly.

## Components

**Compute — Oracle Cloud Always Free + k3s**
Always Free gives 4 OCPU / 24GB RAM of ARM Ampere A1 compute (splittable into up to 4 VMs), 200GB
block storage, and a free reserved public IP — permanently free, not a trial. k3s is a real
Kubernetes distribution (same API/manifests as EKS/GKE/OKE), installed on one Ampere A1 instance to
start. Its default embedded SQLite datastore is single-node-only for the *control plane*; workers
can still scale out, and moving to embedded etcd for control-plane HA is a config flag, not a
redesign — deferred to post-deadline hardening.

**Database — Neon (managed Postgres)**
Chosen over self-hosting Postgres on the cluster: offloads backups/HA/patching entirely, and the
app already talks to Postgres via a `DATABASE_URL` (dialect-portable SQLModel code) — this is a
connection-string swap, not a code change.

**Edge / WAF / TLS — Cloudflare (free plan)**
Replaces Cloud Armor + the external HTTPS LB. Free plan covers DNS, universal TLS, managed WAF
rules, and at least one rate-limiting rule — enough to approximate the current 20 req/min/IP
policy. (Exact free-tier rate-limiting rule count/limits should be re-verified at implementation
time, since Cloudflare's free-tier feature set has changed over time.) TLS between Cloudflare and
the origin uses a Cloudflare Origin CA certificate (free, up to 15-year validity) rather than
`cert-manager`/Let's Encrypt, to avoid a second TLS automation system on day one.

**Image registry — GitHub Container Registry (ghcr.io)**
Replaces Artifact Registry. Free for this use case, and GitHub Actions can push to it with the
built-in `GITHUB_TOKEN` — no new credential to manage.

**CI/CD — GitHub Actions**
Replaces Cloud Build. Workflow builds the existing multi-stage Docker image, pushes to `ghcr.io`,
then runs `kubectl set image` (or `helm upgrade` if the manifests are chart-ized later) against the
k3s cluster using a kubeconfig stored as an encrypted GitHub Actions secret.

**Secrets**
Kubernetes `Secret` objects, applied out-of-band via `kubectl apply -f` from an untracked local
file (mirrors how `.env`/Secret Manager values are handled today) — not committed to git in
plaintext. `sealed-secrets` or SOPS+age for git-safe encrypted secrets is a post-deadline
hardening step, not required for the initial cutover.

## Data flow

**Request path:** client → Cloudflare (TLS terminated, WAF/rate-limit evaluated) → Oracle public
IP (firewalled to Cloudflare ranges) → Traefik ingress (TLS to origin via Origin CA cert) → app
Service → app Pod → Neon over the public internet (`sslmode=require`).

**Deploy path:** push to `main` → GitHub Actions builds image → push to `ghcr.io` tagged with the
commit SHA (continuing the AI-39 SHA-pinning convention, not `:latest`) → `kubectl set image
deployment/ai-dash app=ghcr.io/.../ai-dash:<sha>` against the k3s cluster.

## Phased rollout

Given the hard end-of-month deadline, and that GCP access itself disappears (not just something to
tear down voluntarily), decommissioning must happen with a buffer *before* 2026-07-31, not at the
deadline — there's no opportunity to clean up GCP resources after access is revoked.

1. **Provision Oracle account + Always Free resources now.** Signup/tenancy verification can have
   unpredictable delays; this is the highest schedule-risk step and should start immediately.
2. **Stand up k3s** on one Ampere A1 instance; configure OCI security list to allow 80/443 from
   anywhere (temporary, for validation).
3. **Write k8s manifests** (Deployment, Service, Secret template, Traefik Ingress) and confirm the
   existing Docker image runs correctly against them, DB pointed at a fresh Neon instance seeded
   with a `pg_dump`/`pg_restore` copy of production data. Smoke-test over the node's public IP
   directly (bypassing DNS).
4. **Configure Cloudflare**: add the domain, DNS record (proxied), Full (strict) SSL with an Origin
   CA cert installed on Traefik, a rate-limiting rule approximating the current 20 req/min/IP
   policy.
5. **Cutover**: point production DNS at Cloudflare/Oracle. Keep the GCP stack warm as a rollback
   fallback for a short, explicitly time-boxed window (days, not weeks — bounded by the actual
   access-loss date, not "whenever confidence is high").
6. **Switch CI/CD**: update GitHub Actions to build/push to `ghcr.io` and deploy to k3s; retire the
   Cloud Build pipeline.
7. **Decommission GCP** with margin before 2026-07-31: `terraform destroy` (or manual teardown) of
   Cloud Run, Cloud SQL, Cloud Armor/LB, Secret Manager, Artifact Registry.
8. **Post-deadline hardening** (no rush): second k3s node for HA (embedded etcd), codify the Oracle
   side in Terraform (OCI provider) under `infra/`, `sealed-secrets` for git-safe secrets,
   monitoring (self-hosted Prometheus/Grafana), automated Neon-independent backups if desired,
   and — if/when justified by scale — migrate from self-managed k3s to Oracle's managed OKE control
   plane (same manifests, no app-level rework).

## Testing / verification plan

- **Pre-cutover**: hit the Oracle node's public IP directly with the deployed app; confirm login,
  ingest endpoint, and core dashboard views work against the Neon-backed database with data
  restored from a production `pg_dump`.
- **DB migration integrity**: compare row counts per table between Cloud SQL and Neon after
  restore.
- **Cloudflare path**: confirm `https://dashboard.<domain>` resolves through Cloudflare, TLS is
  valid (Full (strict), no browser warnings), and a quick burst of requests trips the rate-limit
  rule as expected.
- **Firewall restriction**: after Cloudflare is confirmed as the only path in, tighten the OCI
  security list to Cloudflare's IP ranges and verify the origin IP no longer responds directly
  (mirrors the AI-41 verification pattern).
- **CI/CD**: push a trivial commit to `main`, confirm GitHub Actions builds, pushes to `ghcr.io`,
  and the new SHA-tagged image is running on the cluster (`kubectl get deployment -o
  wide`/`describe`).
- **Collector daemon**: confirm it posts successfully to the new domain's `/api/v1/ingest` with no
  code changes.

## Decisions made during brainstorming

- **k3s over full Oracle OKE for the initial cutover**: OKE's control plane is free too, but its
  setup (VCN networking, node pools, IAM policies) is more surface area than is worth risking
  against an 18-day deadline. k3s is real Kubernetes and migrates to OKE later with no manifest
  rewrite, so this is a sequencing decision, not a capability trade-off.
- **Managed Neon Postgres over self-hosting on the cluster**: offloads backup/HA/patching entirely
  given the tight timeline; self-hosting remains an option later if full control becomes a priority.
- **Cloudflare (free) as the edge/WAF layer**, reusing a tool the project already has some history
  with (the Cloudflare Worker path being phased out on the GCP side), rather than standing up
  `cert-manager`/Let's Encrypt plus a separate WAF solution.
- **Decommission GCP with a buffer before the deadline**, not at it — access loss, not voluntary
  teardown, is the actual constraint, so there's no cleanup opportunity after 2026-07-31.
- **Small spend ($5-10/month) is acceptable** for reliability-critical pieces, but nothing in the
  initial cutover plan requires it — all of it fits Oracle's/Neon's/Cloudflare's free tiers. The
  budget headroom is reserved for post-deadline hardening (e.g., a second node).

## Out of scope

- Terraform/IaC for the Oracle side — manual/CLI setup for the initial cutover, formalized after
  the deadline.
- `cert-manager`, Prometheus/Grafana, `sealed-secrets`, and control-plane HA (etcd) — all
  post-deadline hardening.
- Any change to ai_dash application code, the collector daemon's ingest contract, or the
  SQLModel/dialect-portability logic already in place for Postgres.
- Multi-node/autoscaled worker pools — single Ampere A1 node is sufficient for current traffic.
