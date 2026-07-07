# AI-18: Automatic deploy to Cloud Run on merge to main

## Context

Deploys to production (`dash.ai-coordinator.io`, Cloud Run service `ai-dash`) are currently
manual: someone runs `gcloud builds submit --config cloudbuild.yaml .` by hand after merging to
`main`. This was run manually nine times in one session to ship a batch of bug fixes, and it's
easy to merge a PR and forget to deploy it (this happened once already this session). This ticket
wires up automatic deployment so a merge to `main` reliably ships.

## Decisions made during brainstorming

- **CI mechanism: GitHub Actions**, not GCP's native Cloud Build GitHub trigger, chosen to stay
  cloud-agnostic rather than depend on a GCP-proprietary trigger/connection resource.
- **Auth: Workload Identity Federation (WIF)**, not a stored service account JSON key: no
  long-lived credential ever sits in GitHub; nothing to leak or rotate.
- **Deploy gate: a basic build/compile check**, not a full test suite: AI-17 (pytest suite)
  doesn't exist yet and is a separate ticket. The check job catches broken merges (import errors,
  type errors, failed frontend build) without waiting on AI-17. Once AI-17 lands, wiring the real
  test suite into this same `checks` job is a natural follow-up, not part of this ticket.
- **Fully automatic**: no manual approval gate before deploy. Matches how manual deploys have
  worked all along (build, then deploy immediately).
- **Runner: `ubuntu-slim`**: GitHub's 1 vCPU/5GB container-based runner (GA since 2026-01-22).
  Comes with Python 3.12.3, Node.js, Docker, and the Google Cloud CLI pre-installed, and is
  explicitly designed for lightweight jobs like ours (basic compilation, simple scripting); both
  our jobs comfortably fit its 15-minute timeout (manual deploys have consistently taken under 2
  minutes). It has no versioned variant (unlike `ubuntu-24.04`/`ubuntu-22.04`); accepted as a
  rolling GitHub-maintained label rather than falling back to a versioned full-VM runner.
- **Verification approach**: exercise the real steps end-to-end on `main` (push, watch `checks`
  pass, watch `deploy` succeed, confirm the resulting Cloud Run revision). Do **not** deliberately
  break `main` to prove `checks` blocks `deploy`: that dependency (`needs:`) is GitHub Actions'
  own well-tested primitive, not custom logic this ticket introduces, so it doesn't need empirical
  proof the way our own step commands do.

## Architecture

```
git push to main
   │
   ▼
GitHub Actions triggers (.github/workflows/deploy.yml)
   │
   ├─ Job: checks (runs-on: ubuntu-slim)
   │   ├─ backend: pip install -e . && python -c "import backend.main"
   │   └─ frontend: npm ci && npx tsc --noEmit && npm run build
   │
   ▼ (only if checks succeeds, via `needs: checks`)
Job: deploy (runs-on: ubuntu-slim)
   ├─ auth via Workload Identity Federation (google-github-actions/auth, id-token: write)
   └─ gcloud builds submit --config cloudbuild.yaml .
         │
         ▼
      Cloud Build: docker build → push to Artifact Registry → gcloud run deploy
         │
         ▼
      Cloud Run serves the new revision (atomic; prior revision keeps serving until healthy)
```

## Components

### 1. GitHub Actions workflow: `.github/workflows/deploy.yml`

```yaml
name: Deploy
on:
  push:
    branches: [main]

jobs:
  checks:
    runs-on: ubuntu-slim
    steps:
      - uses: actions/checkout@v4
      - run: pip install -e .
      - run: python -c "import backend.main"
      - run: cd frontend && npm ci
      - run: cd frontend && npx tsc --noEmit
      - run: cd frontend && npm run build

  deploy:
    needs: checks
    runs-on: ubuntu-slim
    permissions:
      id-token: write
      contents: read
    steps:
      - uses: actions/checkout@v4
      - uses: google-github-actions/auth@v2
        with:
          workload_identity_provider: <from Terraform output>
          service_account: <from Terraform output>
      - run: gcloud builds submit --config cloudbuild.yaml .
```

### 2. GCP-side Terraform additions: `infra/main.tf`

- `google_iam_workload_identity_pool`: a new WIF pool for GitHub Actions.
- `google_iam_workload_identity_pool_provider`: OIDC provider trusting GitHub's token issuer,
  with an attribute condition scoping it to
  `assertion.repository == "gromano-ltx/ai_dash" && assertion.ref == "refs/heads/main"`; only
  pushes to `main` on this exact repo can mint a usable token (not other repos, not PRs, not
  other branches).
- `google_service_account` (e.g. `github-deployer`): a dedicated service account for this
  purpose, granted `roles/cloudbuild.builds.editor` (enough to submit builds; the build itself
  already runs as Cloud Build's own service account, which already has `roles/run.admin` from the
  existing Terraform).
- `google_service_account_iam_member`: binds the WIF pool's principal to
  `roles/iam.workloadIdentityUser` on the new service account, allowing it to be impersonated only
  from the scoped GitHub identity above.
- New Terraform outputs for `workload_identity_provider` and `service_account` (used to fill in
  the workflow YAML above).

## Error handling

- `checks` fails → `deploy` never runs (`needs:`) → commit shows a red X on GitHub → production
  untouched, still serving the last good revision.
- `gcloud builds submit` fails partway (bad Dockerfile, transient GCP error, etc.) → Cloud Build's
  own atomicity applies: a failed/incomplete build is never deployed, Cloud Run keeps serving the
  prior revision. Same safety property manual deploys have had all session; no new failure mode
  introduced.
- WIF token minting fails (misconfigured attribute condition, wrong repo/branch) → `deploy` job
  fails at the auth step, before ever reaching Cloud Build; no partial/inconsistent state.

## Testing / verification plan

1. Apply the new Terraform (WIF pool, provider, service account, outputs): a real production IAM
   change; pause and confirm with the user before running `terraform apply` even though the
   Terraform plan itself is written as part of this ticket.
2. Add the workflow file, fill in the WIF provider/service-account values from the Terraform
   outputs.
3. Push a real (harmless) commit to `main`, confirm `checks` runs and passes, confirm `deploy`
   runs and succeeds, confirm the resulting Cloud Run revision matches
   `gcloud run services describe ai-dash` output, the same manual verification pattern used all
   session for each deploy.
4. Do not deliberately break `main` to test the `checks`-blocks-`deploy` path (see Decisions
   above).

## Out of scope (this ticket)

- Wiring in the real pytest suite once AI-17 exists (natural follow-up to the `checks` job, not
  part of this ticket).
- Manual approval gates, rollback tooling, or blue/green deployment strategies: deploys stay
  fully automatic and atomic, matching current manual behavior.
- Any change to `cloudbuild.yaml` itself: it's reused as-is.
