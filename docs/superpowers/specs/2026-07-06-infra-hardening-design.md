# AI-39 & AI-40: Pin Cloud Run image tag + Cloud SQL deletion protection

## Context

Two small, independent infra-hardening tickets, bundled into one design since both are
straightforward Terraform/pipeline changes touching the same files:

- **AI-39**: Cloud Run's deployed image is always tagged `:latest`, both in `cloudbuild.yaml`
  (the CI build/push/deploy pipeline from AI-18) and in Terraform's
  `google_cloud_run_v2_service.app` resource. A floating tag means the currently-running image
  can't be identified from a specific commit, and there's no way to redeploy a known-good prior
  version without rebuilding it.
- **AI-40**: `google_sql_database_instance.main` has `deletion_protection = false` (flagged in
  its own comment as `# ponytail: set true in production`), meaning an accidental
  `terraform destroy` or resource-block removal could delete the production database outright.

## Architecture & flow (AI-39)

```
GitHub Actions (deploy.yml)
   │
   ├─ checkout (already happens)
   ├─ compute SHA = short commit SHA (git rev-parse --short HEAD)
   └─ gcloud builds submit --config cloudbuild.yaml
        --substitutions=_TAG=$SHA --async ...
              │
              ▼
       cloudbuild.yaml (unchanged structure, `:latest` dropped):
         build:  docker build -t app:$_TAG .
         push:   docker push app:$_TAG
         deploy: gcloud run deploy --image=app:$_TAG
```

`cloudbuild.yaml` already has a `_TAG` substitution (currently defaulting to `"latest"` for
manual/local runs); no restructuring needed. Two changes: (a) drop the second `-t ...:latest`
build tag and the `:latest` push/images-list entries entirely (no `:latest` tag is pushed going
forward), and (b) `deploy.yml` computes the real commit SHA and passes it explicitly via
`--substitutions=_TAG=$SHA` instead of relying on the default. `gcloud builds submit` uploads an
ad-hoc source tarball rather than running through a Git-based trigger, so Cloud Build's built-in
`$SHORT_SHA`/`$COMMIT_SHA` substitutions aren't populated automatically in this context; the SHA
has to be computed by the workflow itself (which already has the checkout) and passed in.

Every deploy is now traceable to an exact commit and trivially rollback-able (redeploy any prior
SHA tag via `gcloud run deploy --image=...:<old-sha>`).

## Terraform changes (`infra/main.tf`)

1. **AI-39 support**: add a `lifecycle { ignore_changes = [template[0].containers[0].image] }`
   block to `google_cloud_run_v2_service.app`. Terraform still sets
   `image = "${local.image}:latest"` on the very first `terraform apply` (initial creation needs
   *some* image reference to stand up the service), but never touches or reverts it again
   afterward. Without this, a future `terraform apply` (e.g. for AI-40, or any other change) would
   see the CI-deployed SHA-tagged image as drift relative to Terraform's hardcoded `:latest` and
   silently roll the running service back to a stale image. This makes CI the sole owner of what
   image is actually running, post-creation.

2. **AI-40**: flip `deletion_protection = false` to `true` on `google_sql_database_instance.main`
   (`main.tf:81`), removing the now-stale `# ponytail: set true in production` comment. This is a
   metadata-only flag: `terraform apply` shows it as a pure in-place change, with no replacement or
   downtime. Once applied, deleting the instance (via `terraform destroy`, removing the resource
   block, or `gcloud sql instances delete`) requires explicitly disabling deletion protection
   first, as a deliberate, separate step.

## Testing / verification plan

- **AI-40:** `terraform plan` first to confirm a pure in-place change (no destroy/replace) on
  `google_sql_database_instance.main`, then `terraform apply`; verify via
  `gcloud sql instances describe ai-dash-db --format="value(settings.deletionProtectionEnabled)"`
  → `True`.
- **AI-39 (lifecycle block):** apply the `ignore_changes` addition via `terraform apply` (a
  Terraform-only metadata change, with no corresponding GCP-side diff). After a real CI deploy has
  since updated Cloud Run to a SHA-tagged image, run `terraform plan` again and confirm it reports
  **no changes** to the image field, proving Terraform no longer fights CI over it. This is the
  key regression this section exists to prevent.
- **AI-39 (tagging):** push to `main`, let the pipeline run, then verify via
  `gcloud run services describe ai-dash --format="value(spec.template.spec.containers[0].image)"`
  that the deployed image reference ends in the actual commit SHA (not `:latest`), and confirm
  `cloudbuild.yaml` no longer references `:latest` anywhere.

## Decisions made during brainstorming

- Pin tag = git commit SHA (not Cloud Build ID or timestamp), directly traceable to the exact
  code that's running.
- Drop the `:latest` tag entirely rather than keeping it alongside the SHA tag, leaving no floating
  reference in Artifact Registry going forward.
- Terraform's Cloud Run `image` field gets `lifecycle { ignore_changes = [...] }` rather than
  staying fully authoritative: CI owns deploys exclusively after initial creation, avoiding the
  added complexity of CI committing infra changes back to the repo for a project this size.

## Out of scope

- Cleaning up or deleting any pre-existing `:latest` image artifacts already sitting in Artifact
  Registry; this design only stops pushing new ones.
- Any change to how Cloud SQL backups/HA work (that's AI-43, a separate ticket).
- Any change to how secrets are referenced (`:latest` secret versions: that's AI-42, a separate
  ticket, not addressed here despite the superficial "latest" naming overlap).
