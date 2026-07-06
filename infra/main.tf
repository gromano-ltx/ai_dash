terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
  backend "gcs" {
    bucket = "devops-ai-tools-tf-state"
    prefix = "ai-dash"
  }
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
    "servicenetworking.googleapis.com",
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

data "google_compute_network" "default" {
  name = "default"
}

data "google_compute_subnetwork" "default" {
  name   = "default"
  region = var.region
}

# Private services access peering, required for Cloud SQL private IP (AI-44)
resource "google_compute_global_address" "private_ip_range" {
  name          = "${local.service_name}-private-ip"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = data.google_compute_network.default.id
}

resource "google_service_networking_connection" "private_vpc_connection" {
  network                 = data.google_compute_network.default.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_ip_range.name]
  depends_on              = [google_project_service.apis]
}

resource "google_sql_database_instance" "main" {
  name             = "${local.service_name}-db"
  database_version = "POSTGRES_15"
  deletion_protection = true

  settings {
    tier                        = "db-f1-micro" # ponytail: upgrade for >10 users
    deletion_protection_enabled = true
    backup_configuration {
      enabled = true
    }
    ip_configuration {
      ipv4_enabled    = false   # AI-44: private IP only, Cloud Run reaches it via direct VPC egress
      private_network = data.google_compute_network.default.self_link
    }
  }
  depends_on = [google_project_service.apis, google_service_networking_connection.private_vpc_connection]
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
    timeout         = "3600s"

    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [local.db_instance]
      }
    }

    # AI-44: direct VPC egress so Cloud Run can still reach Cloud SQL now that
    # the instance has no public IP.
    vpc_access {
      network_interfaces {
        network    = data.google_compute_network.default.name
        subnetwork = data.google_compute_subnetwork.default.name
      }
      egress = "PRIVATE_RANGES_ONLY"
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

  # AI-39: Terraform only sets the initial image on first-ever creation.
  # After that, CI (cloudbuild.yaml) deploys SHA-pinned images directly via
  # `gcloud run deploy` — without this, a later `terraform apply` (for any
  # unrelated change) would see the CI-deployed image as drift relative to
  # the hardcoded ":latest" below and silently revert the running service
  # to a stale image.
  lifecycle {
    ignore_changes = [template[0].containers[0].image]
  }
}

# Public access — dashboard is protected by DASHBOARD_PASSWORD
resource "google_cloud_run_v2_service_iam_member" "public" {
  location = google_cloud_run_v2_service.app.location
  name     = google_cloud_run_v2_service.app.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

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

# cloudbuild.yaml doesn't specify a custom build service account, so the build
# runs as the default Compute Engine service account. Whoever *submits* a build
# that runs under a given service account needs iam.serviceAccountUser on that
# account (confirmed by an actual live run: "caller does not have permission to
# act as service account ...-compute@developer.gserviceaccount.com"). This is
# separate from cloudbuild_sa_user above, which grants Cloud Build's own
# service agent (not github-deployer, the submitter) project-wide actAs rights.
resource "google_service_account_iam_member" "github_deployer_actas_compute" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/${data.google_project.project.number}-compute@developer.gserviceaccount.com"
  role                = "roles/iam.serviceAccountUser"
  member              = "serviceAccount:${google_service_account.github_deployer.email}"
}

# `gcloud builds submit` tarballs the local source and uploads it to Cloud
# Build's staging bucket before Cloud Build ever runs — needs object CRUD.
resource "google_storage_bucket_iam_member" "github_deployer_staging" {
  bucket = "${var.project_id}_cloudbuild"
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.github_deployer.email}"
}

# gcloud also needs storage.buckets.get to locate/verify the bucket before
# uploading — this bucket has Uniform Bucket-Level Access disabled (legacy
# ACL mode), where that check is enforced separately from object access.
# objectAdmin doesn't include it; legacyBucketReader adds exactly buckets.get
# + read-only listing, without storage.admin's extra bucket-management
# permissions (delete, setIamPolicy, update) that aren't needed here.
resource "google_storage_bucket_iam_member" "github_deployer_staging_reader" {
  bucket = "${var.project_id}_cloudbuild"
  role   = "roles/storage.legacyBucketReader"
  member = "serviceAccount:${google_service_account.github_deployer.email}"
}

# Reproduced the exact failing call locally with --verbosity=debug (impersonating
# github-deployer, against a harmless throwaway build config, no real deploy
# triggered): gcloud builds submit does a project-level bucket search —
# GET .../b?prefix=<bucket>&project=<project> — before ever touching the named
# bucket directly. GCP's own error was explicit: "does not have
# storage.buckets.list access to the Google Cloud project." buckets.list is a
# project-scoped operation (search across all buckets by prefix); no
# bucket-scoped IAM binding can grant it regardless of role — confirmed
# unrelated to the two grants above, which is why they didn't fix this.
# No predefined role grants only this one permission at project scope without
# pulling in unrelated admin capabilities, so a custom role holds exactly it.
resource "google_project_iam_custom_role" "github_deployer_bucket_lister" {
  role_id     = "githubDeployerBucketLister"
  title       = "GitHub Deployer Bucket Lister"
  description = "Exactly storage.buckets.list, for gcloud builds submit's project-level staging-bucket lookup (AI-18)"
  permissions = ["storage.buckets.list"]
}

resource "google_project_iam_member" "github_deployer_bucket_list" {
  project = var.project_id
  role    = google_project_iam_custom_role.github_deployer_bucket_lister.id
  member  = "serviceAccount:${google_service_account.github_deployer.email}"
}

# `gcloud builds submit` streams build logs and polls for completion, which
# requires read access to Cloud Logging (Cloud Build's default log sink).
resource "google_project_iam_member" "github_deployer_logging" {
  project = var.project_id
  role    = "roles/logging.viewer"
  member  = "serviceAccount:${google_service_account.github_deployer.email}"
}

# Discovered by actually running the pipeline: gcloud builds submit attributes
# the source upload's API usage/quota to the project, which requires
# serviceusage.services.use — a distinct permission from bucket object access.
# Without it: "forbidden from accessing the bucket ... check ... if the user
# has the serviceusage.services.use permission".
resource "google_project_iam_member" "github_deployer_serviceusage" {
  project = var.project_id
  role    = "roles/serviceusage.serviceUsageConsumer"
  member  = "serviceAccount:${google_service_account.github_deployer.email}"
}
