# ── Cloud Armor + external HTTPS LB in front of Cloud Run (AI-41) ─────────────
#
# Cloud Armor can't attach directly to a Cloud Run service, so this wires up
# the standard path: serverless NEG -> backend service (w/ Cloud Armor policy)
# -> URL map -> target HTTPS proxy -> global forwarding rule. The Cloud Run
# resource's `ingress` field (see main.tf) is gated behind
# var.restrict_ingress_to_lb so this rollout is a deliberate two-phase
# cutover rather than a single apply that can take the dashboard down.
#
# ── Two-phase cutover runbook ──────────────────────────────────────────────────
#
# Why two phases: the Cloudflare Worker today does `fetch()` straight to the
# Cloud Run URL with a rewritten Host header. The moment Cloud Run's ingress
# is restricted to the LB, that direct URL stops answering — so the Worker
# has to be repointed *before* ingress is restricted, not after. There's also
# a chicken-and-egg problem with the managed SSL cert: it can't go ACTIVE
# until the domain's public DNS actually resolves to the LB's IP, which
# conflicts with Cloudflare currently owning that DNS record via the Worker.
#
# Phase 1 — stand up the new path without touching the old one:
#   1. Apply with the default `restrict_ingress_to_lb = false`. This creates
#      the security policy, NEG, backend service, URL map, managed cert, and
#      forwarding rule, but leaves Cloud Run's ingress at INGRESS_TRAFFIC_ALL
#      — the existing Cloudflare-Worker-to-Cloud-Run path keeps serving
#      traffic completely unchanged.
#   2. `terraform output cloud_armor_lb_ip`.
#   3. Known limitation, confirmed in practice: `curl --resolve ... -k` at
#      this stage does NOT give a usable end-to-end test — while the cert is
#      still PROVISIONING, the LB's HTTPS listener resets the TLS handshake
#      outright (curl error 35 / SSL_ERROR_SYSCALL) rather than presenting
#      any certificate to ignore with `-k`. There is no way to fully
#      validate the HTTPS path before the DNS cutover in step 4 — the
#      cert-activation chicken-and-egg problem described above is real, not
#      just theoretical. Treat step 3 as a structural sanity check only
#      (confirms the LB accepts a TCP connection on 443); the real
#      validation happens after step 5, once the cert is ACTIVE.
#      To confirm the rate-limit rules themselves once HTTPS is actually
#      working (after step 5): fire a request storm and confirm requests
#      past var.cloud_armor_login_rate_limit_per_minute against /api/login
#      get denied with 429, while general traffic has much more headroom
#      (var.cloud_armor_rate_limit_per_minute) — see the two-tier rule
#      design in the security policy below. Don't test the general limit
#      with a tight loop from your own machine without accounting for the
#      frontend's own background polling sharing the same per-IP counter if
#      you have the dashboard open at the same time.
#   4. Once that looks right, replace the Cloudflare Worker with a plain
#      DNS-only ("grey cloud") A record for dash.ai-coordinator.io pointing
#      at cloud_armor_lb_ip (manual step in the Cloudflare dashboard — there's
#      no Cloudflare IaC in this repo). The Worker's Host-rewrite hack is no
#      longer needed once the LB is fronting Cloud Run directly.
#   5. Poll the managed cert until it's ACTIVE — there's a real risk window
#      here where HTTPS requests through the LB hit a cert error, so do this
#      at low-traffic time and watch it closely:
#        gcloud compute ssl-certificates describe ai-dash-ssl-cert --global \
#          --format="value(managed.status)"
#   6. Confirm https://dash.ai-coordinator.io/ now serves correctly through
#      the LB (real DNS this time, no --resolve override needed).
#
# Phase 2 — close off the direct URL (point of no return for the old path):
#   7. Set `restrict_ingress_to_lb = true` and re-apply. This flips Cloud
#      Run's ingress to INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER.
#   8. Confirm the *.run.app URL itself no longer answers directly (should
#      now 404/403 or hang):
#        curl -s -o /dev/null -w "%{http_code}\n" "$(terraform output -raw service_url)"
#   9. Confirm dash.ai-coordinator.io is still healthy through the LB.
#  10. Update the README "Domain" section — it currently documents the old
#      Worker-rewrite setup, which this replaces.
#
# Rollback: before step 7, rollback is trivial — just point Cloudflare's DNS
# back at the Worker; the direct Cloud Run URL was never touched. After step
# 7, rollback means setting restrict_ingress_to_lb back to false and
# re-applying.

# ── Cloud Armor security policy ────────────────────────────────────────────────

resource "google_compute_security_policy" "app" {
  name        = "${local.service_name}-cloud-armor"
  description = "Rate-limits requests to the ${local.service_name} LB (AI-41)"

  # Real-world validation after the Phase 1 cutover (see runbook above) found
  # the original single blanket 20 req/min rule constantly self-triggered:
  # the frontend's own polling (/runs every 5s, /stats every 10s, /daily
  # every 30s) already sustains ~20 req/min from one open tab alone. Split
  # into two tiers instead — a strict, low-priority-number (evaluated first)
  # rule scoped to just /api/login for actual brute-force protection, and a
  # generous blanket rule for everything else so normal polling never trips
  # it.
  rule {
    action   = "throttle"
    priority = 900

    match {
      expr {
        expression = "request.path.matches('^/api/login')"
      }
    }

    rate_limit_options {
      conform_action = "allow"
      exceed_action  = "deny(429)"
      enforce_on_key = "IP"

      rate_limit_threshold {
        count        = var.cloud_armor_login_rate_limit_per_minute
        interval_sec = 60
      }
    }

    description = "Brute-force guard: ${var.cloud_armor_login_rate_limit_per_minute} /api/login attempts/min per client IP"
  }

  # Throttle/deny clients exceeding the general per-IP rate limit (everything
  # not matched by the stricter /api/login rule above).
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
