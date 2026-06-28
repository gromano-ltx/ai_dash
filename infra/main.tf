terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
  # Uncomment to use GCS backend:
  # backend "gcs" {
  #   bucket = "your-tf-state-bucket"
  #   prefix = "ai-dash"
  # }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  service_name = "ai-dash"
  image        = "${var.region}-docker.pkg.dev/${var.project_id}/${local.service_name}/app"
  db_instance  = "${var.project_id}:${var.region}:${local.service_name}-db"
  db_name      = "ai_dash"
  db_user      = "ai_dash"
}

# ── APIs ──────────────────────────────────────────────────────────────────────

resource "google_project_service" "apis" {
  for_each = toset([
    "run.googleapis.com",
    "sqladmin.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "secretmanager.googleapis.com",
  ])
  service            = each.key
  disable_on_destroy = false
}

# ── Artifact Registry ─────────────────────────────────────────────────────────

resource "google_artifact_registry_repository" "app" {
  repository_id = local.service_name
  format        = "DOCKER"
  location      = var.region
  depends_on    = [google_project_service.apis]
}

# ── Cloud SQL ─────────────────────────────────────────────────────────────────

resource "google_sql_database_instance" "main" {
  name             = "${local.service_name}-db"
  database_version = "POSTGRES_15"
  deletion_protection = false   # ponytail: set true in production

  settings {
    tier = "db-f1-micro"        # ponytail: upgrade for >10 users
    backup_configuration {
      enabled = true
    }
    ip_configuration {
      ipv4_enabled = true       # Cloud Run uses Cloud SQL Auth Proxy unix socket
    }
  }
  depends_on = [google_project_service.apis]
}

resource "google_sql_database" "app" {
  name     = local.db_name
  instance = google_sql_database_instance.main.name
}

resource "google_sql_user" "app" {
  name     = local.db_user
  instance = google_sql_database_instance.main.name
  password = var.db_password
}

# ── Secrets ───────────────────────────────────────────────────────────────────

resource "google_secret_manager_secret" "db_url" {
  secret_id = "ai-dash-db-url"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "db_url" {
  secret = google_secret_manager_secret.db_url.id
  secret_data = "postgresql+psycopg2://${local.db_user}:${var.db_password}@/${local.db_name}?host=/cloudsql/${local.db_instance}"
}

resource "google_secret_manager_secret" "dashboard_password" {
  secret_id = "ai-dash-dashboard-password"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "dashboard_password" {
  secret      = google_secret_manager_secret.dashboard_password.id
  secret_data = var.dashboard_password
}

# ── Service Account ───────────────────────────────────────────────────────────

resource "google_service_account" "app" {
  account_id   = local.service_name
  display_name = "ai-dash Cloud Run"
}

resource "google_project_iam_member" "app_cloudsql" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.app.email}"
}

resource "google_secret_manager_secret_iam_member" "db_url" {
  secret_id = google_secret_manager_secret.db_url.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.app.email}"
}

resource "google_secret_manager_secret_iam_member" "dashboard_password" {
  secret_id = google_secret_manager_secret.dashboard_password.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.app.email}"
}

# Grant Cloud Build SA permission to deploy Cloud Run and read Artifact Registry
resource "google_project_iam_member" "cloudbuild_run" {
  project = var.project_id
  role    = "roles/run.admin"
  member  = "serviceAccount:${data.google_project.project.number}@cloudbuild.gserviceaccount.com"
}

resource "google_project_iam_member" "cloudbuild_sa_user" {
  project = var.project_id
  role    = "roles/iam.serviceAccountUser"
  member  = "serviceAccount:${data.google_project.project.number}@cloudbuild.gserviceaccount.com"
}

data "google_project" "project" {}

# ── Cloud Run ─────────────────────────────────────────────────────────────────

resource "google_cloud_run_v2_service" "app" {
  name     = local.service_name
  location = var.region

  template {
    service_account = google_service_account.app.email

    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [local.db_instance]
      }
    }

    containers {
      image = "${local.image}:latest"

      env {
        name = "DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.db_url.secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "DASHBOARD_PASSWORD"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.dashboard_password.secret_id
            version = "latest"
          }
        }
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }
    }
  }

  depends_on = [
    google_project_service.apis,
    google_artifact_registry_repository.app,
    google_sql_database_instance.main,
  ]
}

# Public access — dashboard is protected by DASHBOARD_PASSWORD
resource "google_cloud_run_v2_service_iam_member" "public" {
  location = google_cloud_run_v2_service.app.location
  name     = google_cloud_run_v2_service.app.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
