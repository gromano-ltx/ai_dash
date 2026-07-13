variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region"
  type        = string
  default     = "us-central1"
}

variable "db_password" {
  description = "Cloud SQL postgres user password"
  type        = string
  sensitive   = true
}

variable "dashboard_password" {
  description = "HTTP Basic Auth password for the dashboard"
  type        = string
  sensitive   = true
}

variable "session_secret" {
  description = "Secret key used to sign per-user login session cookies"
  type        = string
  sensitive   = true
}

# ── Cloud Armor (AI-41) ────────────────────────────────────────────────────────

variable "cloud_armor_rate_limit_per_minute" {
  description = <<-EOT
    Max requests allowed per client IP per minute for general dashboard
    traffic (AI-41). Deliberately generous: the frontend's own TanStack
    Query polling (/runs every 5s, /stats every 10s, /daily every 30s)
    already sustains ~20 req/min from a single open tab with zero margin
    for the initial page load, SSE, or a second person behind the same
    NAT — a stricter limit here throttles completely normal usage, not
    abuse. Brute-force protection instead lives in the separate,
    much stricter `cloud_armor_login_rate_limit_per_minute` rule scoped to
    /api/login only.
  EOT
  type        = number
  default     = 300
}

variable "cloud_armor_login_rate_limit_per_minute" {
  description = "Max /api/login requests allowed per client IP per minute — the actual brute-force/credential-stuffing guard (AI-41 follow-up). Kept low since a legitimate user never needs more than a handful of login attempts per minute."
  type        = number
  default     = 10
}

variable "lb_domain" {
  description = <<-EOT
    Domain the external HTTPS LB's managed SSL certificate is issued for (AI-41).
    Defaults to the dashboard's existing domain, but DNS currently points
    `dash.ai-coordinator.io`'s Cloudflare Worker at the Cloud Run URL directly
    (see README "Domain" section) — after apply, DNS/the Worker must be
    manually repointed at the LB's static IP (see the `cloud_armor_lb_ip`
    output) before the managed cert can provision and traffic can flow.
  EOT
  type        = string
  default     = "dash.ai-coordinator.io"
}

variable "restrict_ingress_to_lb" {
  description = <<-EOT
    Second phase of the AI-41 cutover — leave this `false` on the apply that
    first creates the LB/Cloud Armor policy/managed cert, so the existing
    Cloudflare-Worker-to-Cloud-Run path keeps serving traffic unchanged while
    the new path is stood up and validated. Only flip to `true` (and re-apply)
    once: DNS/the Cloudflare Worker has been repointed at `cloud_armor_lb_ip`,
    the managed cert shows `ACTIVE`, and traffic through the LB has been
    confirmed healthy. Flipping this to `true` restricts the Cloud Run
    service's ingress to the LB only, permanently closing the direct
    `*.run.app` URL — see the runbook in cloud_armor.tf for the full sequence.
  EOT
  type        = bool
  default     = false
}

variable "github_token" {
  description = "GitHub PAT (repo or public_repo scope) used to look up PR merge/build status for AI-48's PR success rate stat. Optional — the app degrades gracefully (omits the stat) if left blank."
  type        = string
  sensitive   = true
  default     = ""
}
