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

variable "github_token" {
  description = "GitHub PAT (repo or public_repo scope) used to look up PR merge/build status for AI-48's PR success rate stat. Optional — the app degrades gracefully (omits the stat) if left blank."
  type        = string
  sensitive   = true
  default     = ""
}
