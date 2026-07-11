output "service_url" {
  description = "Cloud Run service URL"
  value       = google_cloud_run_v2_service.app.uri
}

output "registry_image" {
  description = "Artifact Registry image base path"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/ai-dash/app"
}

output "db_instance_connection" {
  description = "Cloud SQL instance connection name"
  value       = google_sql_database_instance.main.connection_name
}

output "workload_identity_provider" {
  description = "Full resource name of the WIF provider for GitHub Actions (AI-18)"
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "github_deployer_service_account" {
  description = "Service account email GitHub Actions impersonates to deploy (AI-18)"
  value       = google_service_account.github_deployer.email
}

output "cloud_armor_lb_ip" {
  description = "Static IP of the external HTTPS LB in front of Cloud Armor / Cloud Run (AI-41). Point DNS for var.lb_domain at this address."
  value       = google_compute_global_address.lb_ip.address
}
