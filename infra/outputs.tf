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
