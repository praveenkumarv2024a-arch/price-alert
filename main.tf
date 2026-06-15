terraform {
  required_version = ">= 1.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

variable "project_id" {
  type        = string
  description = "The Google Cloud Project ID"
}

variable "region" {
  type        = string
  default     = "asia-south1"
  description = "The target GCP region"
}

# 1. Artifact Registry
resource "google_artifact_registry_repository" "repo" {
  location      = var.region
  repository_id = "price-tracker-repo"
  description   = "Docker registry for E-Commerce Price Tracker"
  format        = "DOCKER"
}

# 2. Secret Manager Secrets
resource "google_secret_manager_secret" "telegram_token" {
  secret_id = "TELEGRAM_BOT_TOKEN"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret" "db_url" {
  secret_id = "DATABASE_URL"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret" "api_key" {
  secret_id = "API_KEY"
  replication {
    auto {}
  }
}

# 3. Service Account for Scheduler
resource "google_service_account" "scheduler_sa" {
  account_id   = "scheduler-run-invoker"
  display_name = "Price Tracker Scheduler Caller"
}

# 4. Cloud Run Service (Deployment)
resource "google_cloud_run_service" "tracker_service" {
  name     = "price-tracker"
  location = var.region

  template {
    spec {
      containers {
        # Deploy initial placeholder or built tag
        image = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.repo.repository_id}/price-tracker:latest"
        
        resources {
          limits = {
            memory = "1Gi"
            cpu    = "1000m"
          }
        }

        env {
          name  = "RUN_LOCAL_SCHEDULER"
          value = "false"
        }

        # Mount secrets from Secret Manager
        env {
          name = "TELEGRAM_BOT_TOKEN"
          value_from {
            secret_key_ref {
              name = google_secret_manager_secret.telegram_token.secret_id
              key  = "latest"
            }
          }
        }

        env {
          name = "DATABASE_URL"
          value_from {
            secret_key_ref {
              name = google_secret_manager_secret.db_url.secret_id
              key  = "latest"
            }
          }
        }

        env {
          name = "API_KEY"
          value_from {
            secret_key_ref {
              name = google_secret_manager_secret.api_key.secret_id
              key  = "latest"
            }
          }
        }
      }
    }
  }

  traffic {
    percent         = 100
    latest_revision = true
  }
}

# 5. IAM binding for Cloud Run
resource "google_cloud_run_service_iam_member" "allow_unauthenticated" {
  location = google_cloud_run_service.tracker_service.location
  project  = google_cloud_run_service.tracker_service.project
  service  = google_cloud_run_service.tracker_service.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_service_iam_member" "scheduler_invoker" {
  location = google_cloud_run_service.tracker_service.location
  project  = google_cloud_run_service.tracker_service.project
  service  = google_cloud_run_service.tracker_service.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler_sa.email}"
}

# 6. Cloud Scheduler Cron Job (Pings /api/scrape every 15 minutes)
resource "google_cloud_scheduler_job" "cron_job" {
  name             = "price-tracker-15m-cron"
  description      = "Pings the Cloud Run price crawler endpoint on a 15-minute interval"
  schedule         = "*/15 * * * *"
  time_zone        = "Etc/UTC"
  attempt_deadline = "320s"
  region           = var.region

  http_target {
    http_method = "POST"
    uri         = "${google_cloud_run_service.tracker_service.status[0].url}/api/scrape"
    
    # Authenticate via OIDC token signed by service account
    oidc_token {
      service_account_email = google_service_account.scheduler_sa.email
    }

    # Custom header to pass API key verification
    headers = {
      "X-API-Key" = "REPLACE_WITH_API_KEY_VALUE_IN_HTTP_SCHEDULER"
    }
  }
}

output "service_url" {
  value       = google_cloud_run_service.tracker_service.status[0].url
  description = "The URL of the deployed Price Tracker service"
}
