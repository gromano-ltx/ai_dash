# AI-39 & AI-40 Infra Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable Cloud SQL deletion protection (AI-40) and stop Cloud Run from deploying a floating `:latest` image tag, pinning every deploy to its exact git commit SHA instead (AI-39).

**Architecture:** Two Terraform edits to `infra/main.tf` (a `lifecycle { ignore_changes }` block on the Cloud Run service so CI's SHA-tagged deploys aren't reverted by a later `terraform apply`, and flipping `deletion_protection` to `true` on the Cloud SQL instance), plus a CI pipeline change (`deploy.yml` computes the commit SHA and passes it to `cloudbuild.yaml`, which stops also tagging/pushing `:latest`).

**Tech Stack:** Terraform (`google` provider ~> 5.0), Google Cloud Build, GitHub Actions.

## Global Constraints

- Pin tag = git commit short SHA (not Cloud Build ID, not a timestamp).
- Drop the `:latest` tag entirely: do not keep pushing it alongside the SHA tag.
- Terraform's Cloud Run `image` field must use `lifecycle { ignore_changes = [...] }` so CI owns
  deploys exclusively after the resource's initial creation.
- `deletion_protection = true` on `google_sql_database_instance.main` must be a pure in-place
  Terraform change (no replacement/downtime).

---

### Task 1: Terraform hardening (lifecycle ignore_changes + deletion_protection)

**Files:**
- Modify: `infra/main.tf:80` (`google_sql_database_instance.main`)
- Modify: `infra/main.tf:238-244` (`google_cloud_run_v2_service.app`, end of resource block)

**Interfaces:**
- Consumes: nothing from other tasks; this task is self-contained.
- Produces: nothing other tasks depend on directly, but Task 2's live-deploy verification step
  relies on this task's `lifecycle` block already being applied (otherwise a later `terraform
  apply` would revert Task 2's SHA-tagged deploy back to `:latest`); this task must land and be
  applied to the real GCP project before Task 2's Step 4 (live deploy verification).

- [ ] **Step 1: Flip `deletion_protection` and drop the stale comment**

Replace `infra/main.tf:80`:

```hcl
  deletion_protection = false   # ponytail: set true in production
```

with:

```hcl
  deletion_protection = true
```

- [ ] **Step 2: Add the `lifecycle` block to the Cloud Run service resource**

Replace `infra/main.tf:238-244`:

```hcl
  depends_on = [
    google_project_service.apis,
    google_artifact_registry_repository.app,
    google_sql_database_instance.main,
  ]
}
```

with:

```hcl
  depends_on = [
    google_project_service.apis,
    google_artifact_registry_repository.app,
    google_sql_database_instance.main,
  ]

  # AI-39: Terraform only sets the initial image on first-ever creation.
  # After that, CI (cloudbuild.yaml) deploys SHA-pinned images directly via
  # `gcloud run deploy`; without this, a later `terraform apply` (for any
  # unrelated change) would see the CI-deployed image as drift relative to
  # the hardcoded ":latest" below and silently revert the running service
  # to a stale image.
  lifecycle {
    ignore_changes = [template[0].containers[0].image]
  }
}
```

- [ ] **Step 3: Validate and review the plan**

Run: `cd infra && terraform init -upgrade=false && terraform validate`
Expected: `Success! The configuration is valid.`

Run: `terraform plan`
Expected: Plan shows exactly 2 changes on `google_sql_database_instance.main` (the
`deletion_protection` field going from `false` to `true`) and `google_cloud_run_v2_service.app`
(only the `lifecycle` metadata being added, since Terraform does not treat adding an `ignore_changes`
block as a resource attribute change, so this may show as "0 changes" for that resource in the
plan output; if so, that's expected: the block only affects *future* plans, not this one).
Confirm the SQL instance change is `~ update in-place` (a tilde), never `-/+ destroy and
re-create`.

- [ ] **Step 4: Apply**

Run: `terraform apply` (from the `infra/` directory), confirm with `yes` when prompted.
Expected: `Apply complete! Resources: 0 added, 1 changed, 0 destroyed.` (only the SQL instance
shows as changed; the Cloud Run lifecycle addition is Terraform-local bookkeeping, not a tracked
resource change).

- [ ] **Step 5: Verify deletion protection is live**

Run: `gcloud sql instances describe ai-dash-db --format="value(settings.deletionProtectionEnabled)"`
Expected: `True`

- [ ] **Step 6: Commit**

```bash
git add infra/main.tf
git commit -m "infra: enable Cloud SQL deletion protection, ignore Cloud Run image drift (AI-39, AI-40)"
```

---

### Task 2: Pin Cloud Run deploys to the commit SHA

**Files:**
- Modify: `cloudbuild.yaml`
- Modify: `.github/workflows/deploy.yml`

**Interfaces:**
- Consumes: Task 1's `lifecycle { ignore_changes }` block must already be applied to the real GCP
  project before Step 4 of this task (the live push-to-main deploy); otherwise a stray
  `terraform apply` run any time after this task's deploy could revert Cloud Run back to
  `:latest`.
- Produces: nothing later tasks depend on; this is the final task in the plan.

- [ ] **Step 1: Drop `:latest` from `cloudbuild.yaml`**

Replace the full contents of `cloudbuild.yaml`:

```yaml
substitutions:
  _REGION: us-central1
  _SERVICE: ai-dash
  _TAG: latest

steps:
  - id: build
    name: gcr.io/cloud-builders/docker
    args:
      - build
      - -t
      - ${_REGION}-docker.pkg.dev/${PROJECT_ID}/${_SERVICE}/app:${_TAG}
      - -t
      - ${_REGION}-docker.pkg.dev/${PROJECT_ID}/${_SERVICE}/app:latest
      - .

  - id: push
    name: gcr.io/cloud-builders/docker
    args: [push, --all-tags, "${_REGION}-docker.pkg.dev/${PROJECT_ID}/${_SERVICE}/app"]

  - id: deploy
    name: gcr.io/google.com/cloudsdktool/cloud-sdk:slim
    entrypoint: gcloud
    args:
      - run
      - deploy
      - ${_SERVICE}
      - --image=${_REGION}-docker.pkg.dev/${PROJECT_ID}/${_SERVICE}/app:${_TAG}
      - --region=${_REGION}
      - --platform=managed

images:
  - ${_REGION}-docker.pkg.dev/${PROJECT_ID}/${_SERVICE}/app:${_TAG}
```

with:

```yaml
substitutions:
  _REGION: us-central1
  _SERVICE: ai-dash
  _TAG: latest

steps:
  - id: build
    name: gcr.io/cloud-builders/docker
    args:
      - build
      - -t
      - ${_REGION}-docker.pkg.dev/${PROJECT_ID}/${_SERVICE}/app:${_TAG}
      - .

  - id: push
    name: gcr.io/cloud-builders/docker
    args: [push, "${_REGION}-docker.pkg.dev/${PROJECT_ID}/${_SERVICE}/app:${_TAG}"]

  - id: deploy
    name: gcr.io/google.com/cloudsdktool/cloud-sdk:slim
    entrypoint: gcloud
    args:
      - run
      - deploy
      - ${_SERVICE}
      - --image=${_REGION}-docker.pkg.dev/${PROJECT_ID}/${_SERVICE}/app:${_TAG}
      - --region=${_REGION}
      - --platform=managed

images:
  - ${_REGION}-docker.pkg.dev/${PROJECT_ID}/${_SERVICE}/app:${_TAG}
```

`_TAG` still defaults to `"latest"` for anyone running `gcloud builds submit` manually without
passing `--substitutions`, but CI (Step 2 below) always overrides it with the real commit SHA.
The `-t ...:latest` build tag, the `--all-tags` push (which pushed every tag on the image,
including the now-removed `:latest`), and the `:latest` entry in `images:` are all gone; only the
`${_TAG}`-tagged image is built, pushed, and deployed.

- [ ] **Step 2: Pass the real commit SHA from `deploy.yml`**

Replace this line in `.github/workflows/deploy.yml`:

```yaml
          BUILD_ID=$(gcloud builds submit --config cloudbuild.yaml --async --format="value(id)" .)
```

with:

```yaml
          SHA=$(git rev-parse --short HEAD)
          BUILD_ID=$(gcloud builds submit --config cloudbuild.yaml --substitutions=_TAG="$SHA" --async --format="value(id)" .)
```

- [ ] **Step 3: Validate YAML syntax**

Run: `python3 -c "import yaml; yaml.safe_load(open('cloudbuild.yaml')); yaml.safe_load(open('.github/workflows/deploy.yml')); print('valid YAML')"`
Expected: `valid YAML`

- [ ] **Step 4: Commit, push, and verify a real live deploy**

```bash
git add cloudbuild.yaml .github/workflows/deploy.yml
git commit -m "ci: pin Cloud Run deploys to the commit SHA instead of :latest (AI-39)"
git push -u origin <branch-name>
```

Open a PR, wait for checks to pass, and, once merged by the user to `main`, confirm the live
deploy:

Run: `gcloud run services describe ai-dash --region=us-central1 --format="value(spec.template.spec.containers[0].image)"`
Expected: the image reference ends in a 7-character hex string matching
`git rev-parse --short HEAD` for the merge commit on `main` (not `:latest`).

- [ ] **Step 5: Confirm Terraform no longer sees the deployed image as drift**

Run: `cd infra && terraform plan`
Expected: the plan reports no changes to `google_cloud_run_v2_service.app`'s `image` field, even
though it now differs from the hardcoded `${local.image}:latest` in `main.tf`, proving Task 1's
`lifecycle { ignore_changes }` block is working as intended. If this step shows a change to the
image field, Task 1's lifecycle block did not apply correctly and must be fixed before proceeding.

---

## Self-Review

**Spec coverage:** Architecture & flow (SHA substitution, cloudbuild.yaml simplification) → Task
2. Terraform `lifecycle { ignore_changes }` → Task 1 Step 2. `deletion_protection` flip → Task 1
Step 1. All three testing/verification bullets from the spec are covered by Task 1 Steps 3-5 and
Task 2 Steps 4-5.

**Placeholder scan:** No TBD/TODO; every step shows complete file contents or exact diffs and
commands with expected output.

**Type consistency:** N/A (Terraform/YAML/bash, no shared function signatures across tasks); the
only cross-task dependency is Task 1's `lifecycle` block existing in the real GCP project before
Task 2 Step 4/5 run, called out explicitly in both tasks' Interfaces sections.
