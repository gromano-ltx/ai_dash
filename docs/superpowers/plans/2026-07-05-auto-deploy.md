# AI-18 Auto-Deploy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically deploy to Cloud Run whenever a commit lands on `main`, replacing the manual `gcloud builds submit --config cloudbuild.yaml .` that's been run by hand all session.

**Architecture:** A GitHub Actions workflow (`.github/workflows/deploy.yml`) triggers on push to `main`. A `checks` job (backend import check + frontend typecheck/build) must pass before a `deploy` job runs. `deploy` authenticates to GCP via Workload Identity Federation (no stored credentials) and re-invokes the existing `cloudbuild.yaml` pipeline unchanged.

**Tech Stack:** GitHub Actions (`ubuntu-slim` runner), Terraform (`google_iam_workload_identity_pool`/`google_iam_workload_identity_pool_provider`/`google_service_account`), `google-github-actions/auth@v2`, existing `cloudbuild.yaml`.

## Global Constraints

- WIF provider must be scoped to exactly `repo:gromano-ltx/ai_dash` on `ref:refs/heads/main`: no other repo or branch may mint a usable token.
- No long-lived GCP credential is ever stored in GitHub (this is the entire point of using WIF over a service account key).
- `cloudbuild.yaml` is reused unchanged: do not duplicate its build steps in the workflow.
- Deploy gate is a basic build/compile check only (no test suite exists yet; AI-17 is separate).
- Fully automatic: no manual approval step before deploy.
- `runs-on: ubuntu-slim` for both jobs (GA GitHub-hosted runner, Python/Node/gcloud pre-installed).
- Full design context: `docs/superpowers/specs/2026-07-05-auto-deploy-design.md`.

---

### Task 1: Write and validate the Workload Identity Federation Terraform (no apply yet)

**Files:**
- Modify: `infra/main.tf` (append new resources at end of file)
- Modify: `infra/outputs.tf` (append two new outputs)

**Interfaces:**
- Produces: Terraform resources `google_iam_workload_identity_pool.github`,
  `google_iam_workload_identity_pool_provider.github`, `google_service_account.github_deployer`;
  their attributes (`.name`, `.email`) are consumed by Task 2's `terraform output` commands.

- [ ] **Step 1: Append the WIF pool, provider, service account, and IAM bindings to `infra/main.tf`**

Add this block at the end of `infra/main.tf`:

```hcl
# ── GitHub Actions Workload Identity Federation (AI-18 auto-deploy) ────────────

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github-actions"
  display_name              = "GitHub Actions"
  depends_on                = [google_project_service.apis]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  display_name                       = "GitHub"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }
  # Only pushes to main on this exact repo can mint a usable token.
  attribute_condition = "assertion.repository == \"gromano-ltx/ai_dash\" && assertion.ref == \"refs/heads/main\""

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account" "github_deployer" {
  account_id   = "github-deployer"
  display_name = "GitHub Actions deployer (AI-18)"
}

# Enough to submit builds; the build itself runs as Cloud Build's own service
# account, which already has roles/run.admin from the existing Terraform above.
resource "google_project_iam_member" "github_deployer_cloudbuild" {
  project = var.project_id
  role    = "roles/cloudbuild.builds.editor"
  member  = "serviceAccount:${google_service_account.github_deployer.email}"
}

resource "google_service_account_iam_member" "github_deployer_wif" {
  service_account_id = google_service_account.github_deployer.name
  role                = "roles/iam.workloadIdentityUser"
  member              = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/gromano-ltx/ai_dash"
}
```

- [ ] **Step 2: Append the two new outputs to `infra/outputs.tf`**

Add this block at the end of `infra/outputs.tf`:

```hcl
output "workload_identity_provider" {
  description = "Full resource name of the WIF provider for GitHub Actions (AI-18)"
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "github_deployer_service_account" {
  description = "Service account email GitHub Actions impersonates to deploy (AI-18)"
  value       = google_service_account.github_deployer.email
}
```

- [ ] **Step 3: Validate the Terraform syntax**

Run: `cd infra && terraform validate`
Expected: `Success! The configuration is valid.`

- [ ] **Step 4: Plan the changes (no apply)**

Run: `cd infra && terraform plan`
Expected: Plan shows `4 to add` (pool, provider, service account, project IAM member) and `1 to add` for the service account IAM member (5 resources to add total, 0 to change, 0 to destroy). No existing resources should show as changed or destroyed. If anything shows as destroyed, stop and re-check Step 1 before proceeding.

- [ ] **Step 5: Commit**

```bash
git add infra/main.tf infra/outputs.tf
git commit -m "infra: add Workload Identity Federation for GitHub Actions deploy (AI-18)"
```

---

### Task 2: Apply the Terraform and capture the WIF values

This task makes a real change to production GCP IAM. **Pause here and get the user's explicit
go-ahead before running `terraform apply`**, even though Task 1 already validated the plan.

**Files:** none (infrastructure-only task; no repo files change)

**Interfaces:**
- Consumes: the Terraform resources from Task 1.
- Produces: two string values, `workload_identity_provider` and `github_deployer_service_account`,
  that Task 3 substitutes verbatim into the workflow YAML.

- [ ] **Step 1: Get explicit user confirmation**

Ask the user directly: "About to run `terraform apply` in `infra/`, which will create a new Workload Identity Pool, OIDC provider, and service account in the `devops-ai-tools` GCP project. This is a real production IAM change. OK to proceed?" Do not continue to Step 2 until they say yes.

- [ ] **Step 2: Apply**

Run: `cd infra && terraform apply`
Expected: prompts `Do you want to perform these actions?`; type `yes`. Ends with `Apply complete! Resources: 5 added, 0 changed, 0 destroyed.`

- [ ] **Step 3: Capture the two output values**

Run: `cd infra && terraform output -raw workload_identity_provider`
Expected: a string like `projects/123456789/locations/global/workloadIdentityPools/github-actions/providers/github`

Run: `cd infra && terraform output -raw github_deployer_service_account`
Expected: a string like `github-deployer@devops-ai-tools.iam.gserviceaccount.com`

Write both values down exactly: Task 3 needs them verbatim.

- [ ] **Step 4: No commit needed**

This task only ran `terraform apply` against already-committed configuration from Task 1; there's nothing new to commit.

---

### Task 3: Create the GitHub Actions workflow

**Files:**
- Create: `.github/workflows/deploy.yml`

**Interfaces:**
- Consumes: `workload_identity_provider` and `github_deployer_service_account` string values captured in Task 2, Step 3.

- [ ] **Step 1: Create the workflow file**

Create `.github/workflows/deploy.yml`. Replace `<WORKLOAD_IDENTITY_PROVIDER>` and
`<SERVICE_ACCOUNT_EMAIL>` below with the exact values captured in Task 2:

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

      - name: Backend import check
        run: |
          pip install -e .
          python -c "import backend.main"

      - name: Frontend typecheck + build
        working-directory: frontend
        run: |
          npm ci
          npx tsc --noEmit
          npm run build

  deploy:
    needs: checks
    runs-on: ubuntu-slim
    permissions:
      id-token: write
      contents: read
    steps:
      - uses: actions/checkout@v4

      - id: auth
        uses: google-github-actions/auth@v2
        with:
          workload_identity_provider: "<WORKLOAD_IDENTITY_PROVIDER>"
          service_account: "<SERVICE_ACCOUNT_EMAIL>"

      - name: Deploy via Cloud Build
        run: gcloud builds submit --config cloudbuild.yaml .
```

- [ ] **Step 2: Validate the YAML is well-formed**

Run: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/deploy.yml'))" && echo "valid YAML"`
Expected: `valid YAML`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/deploy.yml
git commit -m "ci: add GitHub Actions auto-deploy workflow (AI-18)"
```

---

### Task 4: End-to-end verification on `main`

**Files:** none (verification only)

**Interfaces:**
- Consumes: the merged workflow from Task 3 and the applied infra from Task 2.

- [ ] **Step 1: Push Task 1-3's commits to `main`**

Run: `git push origin main`
Expected: push succeeds; this push itself will trigger the new workflow.

- [ ] **Step 2: Watch the `checks` job**

Run: `gh run watch --exit-status` (run this right after the push; it attaches to the most recent run)
Expected: the `checks` job completes successfully (backend import + frontend typecheck/build all pass).

- [ ] **Step 3: Confirm the `deploy` job ran and succeeded**

Run: `gh run view --log | grep -A5 "gcloud builds submit"`
Expected: log output shows the Cloud Build steps (`build`, `push`, `deploy`) completing with `STATUS: SUCCESS`, matching the output seen from every manual `gcloud builds submit` run earlier in the project's history.

- [ ] **Step 4: Confirm the live Cloud Run revision updated**

Run: `gcloud run services describe ai-dash --region us-central1 --format="value(status.latestReadyRevisionName, status.traffic)"`
Expected: a new revision name (higher number than whatever was live before Task 4, Step 1) at `100` percent traffic.

- [ ] **Step 5: Confirm no errors in the new revision's logs**

Run: `gcloud run services logs read ai-dash --region us-central1 --limit=30 | grep -iE "error|traceback"`
Expected: no output (no errors).

No commit for this task: it's pure verification of Tasks 1-3's already-committed work.
