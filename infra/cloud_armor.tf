# ── Cloud Armor + external HTTPS LB in front of Cloud Run (AI-41) ─────────────
#
# Cloud Armor can't attach directly to a Cloud Run service, so this wires up
# the standard path: serverless NEG -> backend service (w/ Cloud Armor policy)
# -> URL map -> target HTTPS proxy -> global forwarding rule. The Cloud Run
# resource's `ingress` field (see main.tf) is restricted to
# INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER, so this LB is the only path in.
#
# ── Post-apply verification runbook (rate limiting, DoD item) ─────────────────
#
# 1. `terraform apply`, then `terraform output cloud_armor_lb_ip`.
# 2. Point DNS for var.lb_domain at that IP (see the variable's description —
#    today dash.ai-coordinator.io's Cloudflare Worker targets Cloud Run
#    directly and needs to be repointed at this LB instead).
# 3. Wait for the managed SSL cert to finish provisioning:
#      gcloud compute ssl-certificates describe ai-dash-ssl-cert --global \
#        --format="value(managed.status)"
#    (must show ACTIVE before HTTPS requests will succeed).
# 4. Fire a request storm and confirm requests past #20 in a rolling minute
#    get denied with 429:
#      for i in $(seq 1 30); do
#        curl -s -o /dev/null -w "%{http_code}\n" "https://<lb_domain>/"
#      done
#    Expected: the first ~20 responses are whatever the app normally returns
#    (200/302/401 depending on auth state), and responses from #21 onward in
#    that same minute are 429 until the window rolls over.
# 5. Confirm the *.run.app URL itself no longer answers directly (should now
#    404/403 or hang, since ingress is restricted to the LB only):
#      curl -s -o /dev/null -w "%{http_code}\n" "$(terraform output -raw service_url)"

# ── Cloud Armor security policy ────────────────────────────────────────────────

resource "google_compute_security_policy" "app" {
  name        = "${local.service_name}-cloud-armor"
  description = "Rate-limits requests to the ${local.service_name} LB (AI-41)"

  # Throttle/deny clients exceeding the per-IP rate limit.
  rule {
    action   = "throttle"
    priority = 1000

    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }

    rate_limit_options {
      conform_action = "allow"
      exceed_action  = "deny(429)"
      enforce_on_key = "IP"

      rate_limit_threshold {
        count        = var.cloud_armor_rate_limit_per_minute
        interval_sec = 60
      }
    }

    description = "Rate limit: ${var.cloud_armor_rate_limit_per_minute} req/min per client IP"
  }

  # Required default rule — allow everything not otherwise matched above.
  rule {
    action   = "allow"
    priority = 2147483647

    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }

    description = "Default allow"
  }
}

# ── Serverless NEG pointing at the Cloud Run service ───────────────────────────

resource "google_compute_region_network_endpoint_group" "app" {
  name                  = "${local.service_name}-serverless-neg"
  region                = var.region
  network_endpoint_type = "SERVERLESS"

  cloud_run {
    service = google_cloud_run_v2_service.app.name
  }
}

# ── Backend service (Cloud Armor attaches here) ────────────────────────────────

resource "google_compute_backend_service" "app" {
  name                  = "${local.service_name}-backend"
  load_balancing_scheme = "EXTERNAL"
  security_policy       = google_compute_security_policy.app.id

  backend {
    group = google_compute_region_network_endpoint_group.app.id
  }
}

# ── URL map / HTTPS proxy / forwarding rule ────────────────────────────────────

resource "google_compute_url_map" "app" {
  name            = "${local.service_name}-url-map"
  default_service = google_compute_backend_service.app.id
}

resource "google_compute_managed_ssl_certificate" "app" {
  name = "${local.service_name}-ssl-cert"

  managed {
    domains = [var.lb_domain]
  }
}

resource "google_compute_target_https_proxy" "app" {
  name             = "${local.service_name}-https-proxy"
  url_map          = google_compute_url_map.app.id
  ssl_certificates = [google_compute_managed_ssl_certificate.app.id]
}

resource "google_compute_global_address" "lb_ip" {
  name = "${local.service_name}-lb-ip"
}

resource "google_compute_global_forwarding_rule" "app" {
  name                  = "${local.service_name}-https-fr"
  target                = google_compute_target_https_proxy.app.id
  port_range            = "443"
  ip_address            = google_compute_global_address.lb_ip.address
  load_balancing_scheme = "EXTERNAL"
}
