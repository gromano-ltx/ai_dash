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
  description = "Max requests allowed per client IP per minute at the Cloud Armor policy in front of the LB, before requests are throttled/denied (AI-41)"
  type        = number
  default     = 20
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
