# Migrate ai_dash off GCP to Oracle Cloud + k3s Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move ai_dash's production deployment off GCP (Cloud Run + Cloud SQL + Cloud Armor) onto Oracle Cloud Always Free compute running k3s, with Neon for Postgres and Cloudflare for edge/TLS/WAF, before GCP org access is lost.

**Architecture:** A single k3s node (Oracle Ampere A1, Always Free) runs the existing `ai_dash` Docker image as a Kubernetes Deployment, fronted by k3s's built-in Traefik ingress terminating TLS with a Cloudflare Origin CA certificate. Cloudflare (already the DNS host for `dash.ai-coordinator.io`) proxies traffic in front, providing TLS-to-client, WAF, and rate limiting. The app connects to a Neon-managed Postgres instance instead of Cloud SQL. GitHub Actions builds/pushes to GHCR and deploys via `kubectl`.

**Tech Stack:** Oracle Cloud Infrastructure (OCI), k3s, Neon Postgres, Cloudflare, GitHub Container Registry (ghcr.io), GitHub Actions, kubectl.

## Global Constraints

- GCP org access ends 2026-07-31 — GCP resources must be decommissioned *before* this date, with a buffer (not on the last day).
- Baseline cost target is $0/month; up to ~$5-10/month is acceptable only for genuine reliability wins, not required by any task in this plan.
- Real Kubernetes (k3s), not a Kubernetes-flavored abstraction — manifests must be plain, portable k8s objects (no k3s/Traefik-specific CRDs) so a later move to Oracle's managed OKE needs no rewrite.
- Domain is `dash.ai-coordinator.io`, already a Cloudflare-managed zone (registrar is Squarespace) — no nameserver migration needed, only a DNS record change.
- Existing app tables: `agent_runs`, `transcript_store`, `api_keys`, `users` (from `backend/models.py`) — used for migration row-count verification.
- This is infra/ops work, not application code: "tests" in this plan are verification shell commands, following the same convention as `docs/superpowers/specs/2026-07-06-infra-hardening-design.md` (no pytest involved).
- Full design context: `docs/superpowers/specs/2026-07-13-oracle-k3s-migration-design.md`.

---

### Task 1: Provision Oracle Cloud Always Free compute + networking

**Files:** None (cloud console/CLI only — Terraform for this side is explicitly out of scope, see design doc).

**Interfaces:**
- Produces: a reachable Ampere A1 instance at a **reserved public IP** (call it `$ORACLE_IP` in later tasks), reachable via SSH, with security-list rules allowing inbound 22 (SSH), 80/443 (HTTP/HTTPS, temporarily open to `0.0.0.0/0`, tightened in Task 7), and 6443 (k3s API).

- [ ] **Step 1: Create an Oracle Cloud "Always Free" account** (if not already done) at oracle.com/cloud/free — this can involve identity/card verification with unpredictable turnaround, so do this first, today.

- [ ] **Step 2: Create the compute instance via the OCI Console**
  - Compute > Instances > Create instance.
  - Shape: `VM.Standard.A1.Flex` (Ampere, Always Free-eligible) — 2 OCPU / 12GB RAM is enough headroom for k3s + the app; leaves room to add a second instance later within the 4 OCPU/24GB Always Free allocation.
  - Image: Ubuntu 22.04 (Always Free-eligible, most common for k3s docs).
  - Boot volume: default (Always Free covers up to 200GB total across volumes).
  - Add your SSH public key under "Add SSH keys."
  - Networking: use the default VCN/subnet created by Oracle's "create VCN" quick option if this is a fresh tenancy.
  - Create the instance; note its public IP.

- [ ] **Step 3: Reserve the public IP** (so it survives instance stop/start)
  - Networking > IP Management > Reserved Public IPs > Create Reserved Public IP.
  - Assign it to the instance's VNIC, replacing the ephemeral IP.
  - This becomes `$ORACLE_IP` for every later task.

- [ ] **Step 4: Open required ports in the security list**
  - Networking > Virtual Cloud Networks > (your VCN) > Security Lists > (default list) > Add Ingress Rules.
  - Add: `0.0.0.0/0` → TCP `80`, `443` (temporary — Task 7 narrows this to Cloudflare's ranges).
  - Add: `0.0.0.0/0` → TCP `6443` (k3s API). This is safe to leave open because k3s enforces mutual-TLS client-certificate auth by default — reaching the port grants nothing without the client cert issued at cluster creation (used by GitHub Actions in Task 8). Restrict SSH (22) to your own IP/CIDR if you have a stable one, since SSH accepts more attack surface (password/key-guessing) than an unauthenticated port.
  - Oracle's Ubuntu images also run local `iptables` rules independent of the security list — on the instance, run:
    ```bash
    sudo iptables -I INPUT 6 -p tcp --dport 80 -j ACCEPT
    sudo iptables -I INPUT 6 -p tcp --dport 443 -j ACCEPT
    sudo iptables -I INPUT 6 -p tcp --dport 6443 -j ACCEPT
    sudo netfilter-persistent save
    ```

- [ ] **Step 5: Verify reachability**
  ```bash
  ssh -i ~/.ssh/<your-key> ubuntu@$ORACLE_IP echo ok
  ```
  Expected: prints `ok`.

---

### Task 2: Install k3s and confirm the cluster is up

**Files:** None.

**Interfaces:**
- Consumes: `$ORACLE_IP`, SSH access from Task 1.
- Produces: a working k3s cluster; a local kubeconfig file (`~/.kube/ai-dash-k3s.yaml`) used by every later `kubectl` command in this plan.

- [ ] **Step 1: Install k3s on the instance**
  ```bash
  ssh ubuntu@$ORACLE_IP 'curl -sfL https://get.k3s.io | sh -'
  ```
  Expected: installs and starts `k3s.service`.

- [ ] **Step 2: Verify the node is Ready**
  ```bash
  ssh ubuntu@$ORACLE_IP 'sudo k3s kubectl get nodes'
  ```
  Expected: one node listed with `STATUS Ready`.

- [ ] **Step 3: Pull the kubeconfig locally and point it at the public IP**
  ```bash
  scp ubuntu@$ORACLE_IP:/etc/rancher/k3s/k3s.yaml ~/.kube/ai-dash-k3s.yaml
  sed -i '' "s/127.0.0.1/$ORACLE_IP/" ~/.kube/ai-dash-k3s.yaml   # macOS sed; drop '' arg on Linux
  ```

- [ ] **Step 4: Verify local kubectl access**
  ```bash
  KUBECONFIG=~/.kube/ai-dash-k3s.yaml kubectl get nodes
  ```
  Expected: same Ready node, now reachable from your machine over `$ORACLE_IP:6443`.

---

### Task 3: Add in-repo k8s manifests and deploy the existing image

**Files:**
- Create: `infra/k8s/deployment.yaml`
- Create: `infra/k8s/service.yaml`
- Create: `infra/k8s/secret.example.yaml`

**Interfaces:**
- Consumes: the existing Dockerfile (`Dockerfile`, unmodified — already builds and serves on `$PORT`, default `8080`).
- Produces: Deployment `ai-dash` (container name `app`, port `8080`), Service `ai-dash` (port `8080`) — these exact names are referenced by Task 5's Ingress and Task 8's CI/CD deploy step.

- [ ] **Step 1: Make the GHCR package public** (avoids needing an `imagePullSecret` — the source repo is already public, so this adds no exposure)
  - After the first push in Step 2, go to `github.com/gromano-ltx/ai_dash/pkgs/container/ai_dash` > Package settings > Change visibility > Public.

- [ ] **Step 2: Build and push the current image manually** (first image, before CI/CD is wired up in Task 8)
  ```bash
  git rev-parse --short HEAD   # note this SHA, call it $SHA
  docker build -t ghcr.io/gromano-ltx/ai_dash:$SHA .
  echo $GITHUB_TOKEN | docker login ghcr.io -u gromano-ltx --password-stdin
  docker push ghcr.io/gromano-ltx/ai_dash:$SHA
  ```

- [ ] **Step 3: Write `infra/k8s/deployment.yaml`**
  ```yaml
  apiVersion: apps/v1
  kind: Deployment
  metadata:
    name: ai-dash
  spec:
    replicas: 1
    selector:
      matchLabels:
        app: ai-dash
    template:
      metadata:
        labels:
          app: ai-dash
      spec:
        containers:
          - name: app
            image: ghcr.io/gromano-ltx/ai_dash:REPLACE_WITH_SHA
            ports:
              - containerPort: 8080
            env:
              - name: PORT
                value: "8080"
              - name: DATABASE_URL
                valueFrom:
                  secretKeyRef:
                    name: ai-dash-secrets
                    key: DATABASE_URL
              - name: SESSION_SECRET
                valueFrom:
                  secretKeyRef:
                    name: ai-dash-secrets
                    key: SESSION_SECRET
  ```
  (`REPLACE_WITH_SHA` is a real placeholder here — swap it for the actual `$SHA` from Step 2 before applying; Task 8 automates this going forward via `kubectl set image`.)

- [ ] **Step 4: Write `infra/k8s/service.yaml`**
  ```yaml
  apiVersion: v1
  kind: Service
  metadata:
    name: ai-dash
  spec:
    selector:
      app: ai-dash
    ports:
      - port: 8080
        targetPort: 8080
  ```

- [ ] **Step 5: Write `infra/k8s/secret.example.yaml`** (template only — documents the shape, never applied as-is or committed with real values)
  ```yaml
  apiVersion: v1
  kind: Secret
  metadata:
    name: ai-dash-secrets
  type: Opaque
  stringData:
    DATABASE_URL: "postgresql+psycopg2://<user>:<password>@<neon-host>/<db>?sslmode=require"
    SESSION_SECRET: "<generate with: openssl rand -hex 32>"
  ```

- [ ] **Step 6: Create the real secret out-of-band** (not from git — use a placeholder `DATABASE_URL` for now; Task 4 replaces it with the real Neon connection string)
  ```bash
  KUBECONFIG=~/.kube/ai-dash-k3s.yaml kubectl create secret generic ai-dash-secrets \
    --from-literal=DATABASE_URL="sqlite:////tmp/placeholder.db" \
    --from-literal=SESSION_SECRET="$(openssl rand -hex 32)"
  ```

- [ ] **Step 7: Apply the manifests** (deployment.yaml with the real `$SHA` substituted)
  ```bash
  KUBECONFIG=~/.kube/ai-dash-k3s.yaml kubectl apply -f infra/k8s/deployment.yaml -f infra/k8s/service.yaml
  ```

- [ ] **Step 8: Verify the pod is running**
  ```bash
  KUBECONFIG=~/.kube/ai-dash-k3s.yaml kubectl get pods -l app=ai-dash
  KUBECONFIG=~/.kube/ai-dash-k3s.yaml kubectl port-forward svc/ai-dash 8080:8080
  curl -s localhost:8080/ | head -c 200
  ```
  Expected: pod `STATUS Running`, and the `curl` returns the app's HTML (placeholder DB is fine at this point — this step only proves the container starts and serves).

- [ ] **Step 9: Commit the manifests**
  ```bash
  git add infra/k8s/deployment.yaml infra/k8s/service.yaml infra/k8s/secret.example.yaml
  git commit -m "Add k3s manifests for ai_dash Deployment/Service"
  ```

---

### Task 4: Provision Neon Postgres and migrate data from Cloud SQL

**Files:** None (data migration + secret update only).

**Interfaces:**
- Consumes: `ai-dash-secrets` Secret from Task 3 (updates `DATABASE_URL` key).
- Produces: a live Neon connection string, verified to contain the same row counts as production Cloud SQL.

- [ ] **Step 1: Create a Neon project** (neon.tech, free tier) and note the connection string as `$NEON_DATABASE_URL` (format: `postgresql://<user>:<password>@<host>/<db>?sslmode=require`).

- [ ] **Step 2: Get the Cloud SQL connection name**
  ```bash
  cd infra && terraform output db_instance_connection
  ```
  Note this as `$CONNECTION_NAME` (format `PROJECT:REGION:INSTANCE`).

- [ ] **Step 3: Tunnel to Cloud SQL via the Auth Proxy**
  ```bash
  cloud-sql-proxy --port 5433 "$CONNECTION_NAME" &
  ```

- [ ] **Step 4: Dump production data**
  ```bash
  pg_dump --no-owner --no-acl -Fc -h localhost -p 5433 -U postgres ai_dash -f ai_dash_migration.dump
  ```
  (Adjust `-U` to the actual DB user if different — check `DATABASE_URL` in Secret Manager or `infra/main.tf`'s `google_sql_user` resource if unsure.)

- [ ] **Step 5: Restore into Neon**
  ```bash
  pg_restore --no-owner --no-acl -d "$NEON_DATABASE_URL" ai_dash_migration.dump
  ```

- [ ] **Step 6: Verify row counts match**
  ```bash
  psql -h localhost -p 5433 -U postgres ai_dash -c \
    "SELECT 'agent_runs', count(*) FROM agent_runs UNION ALL SELECT 'transcript_store', count(*) FROM transcript_store UNION ALL SELECT 'api_keys', count(*) FROM api_keys UNION ALL SELECT 'users', count(*) FROM users;"
  psql "$NEON_DATABASE_URL" -c \
    "SELECT 'agent_runs', count(*) FROM agent_runs UNION ALL SELECT 'transcript_store', count(*) FROM transcript_store UNION ALL SELECT 'api_keys', count(*) FROM api_keys UNION ALL SELECT 'users', count(*) FROM users;"
  ```
  Expected: identical counts per table on both sides.

- [ ] **Step 7: Point the cluster at Neon**
  ```bash
  KUBECONFIG=~/.kube/ai-dash-k3s.yaml kubectl delete secret ai-dash-secrets
  KUBECONFIG=~/.kube/ai-dash-k3s.yaml kubectl create secret generic ai-dash-secrets \
    --from-literal=DATABASE_URL="$NEON_DATABASE_URL" \
    --from-literal=SESSION_SECRET="$(openssl rand -hex 32)"
  KUBECONFIG=~/.kube/ai-dash-k3s.yaml kubectl rollout restart deployment/ai-dash
  ```

- [ ] **Step 8: Verify the app works against Neon**
  ```bash
  KUBECONFIG=~/.kube/ai-dash-k3s.yaml kubectl port-forward svc/ai-dash 8080:8080
  curl -s localhost:8080/api/v1/runs -H "X-API-Key: <an existing admin's key>" | head -c 300
  ```
  Expected: returns real run data migrated from production, not an empty/error response.

---

### Task 5: TLS termination — Cloudflare Origin CA cert + Ingress

**Files:**
- Create: `infra/k8s/ingress.yaml`

**Interfaces:**
- Consumes: Service `ai-dash` (port 8080) from Task 3.
- Produces: Ingress `ai-dash` routing `dash.ai-coordinator.io` to the app over HTTPS, using TLS secret `ai-dash-tls`.

- [ ] **Step 1: Generate a Cloudflare Origin CA certificate** (Cloudflare dashboard > SSL/TLS > Origin Server > Create Certificate)
  - Hostname: `dash.ai-coordinator.io`
  - Validity: 15 years
  - Download `cert.pem` and `key.pem` (do not commit these to git).

- [ ] **Step 2: Create the TLS secret from the downloaded files**
  ```bash
  KUBECONFIG=~/.kube/ai-dash-k3s.yaml kubectl create secret tls ai-dash-tls --cert=cert.pem --key=key.pem
  ```

- [ ] **Step 3: Write `infra/k8s/ingress.yaml`** (plain `networking.k8s.io/v1` Ingress — no Traefik-specific CRDs, so this is portable to any ingress controller, including OKE's later)
  ```yaml
  apiVersion: networking.k8s.io/v1
  kind: Ingress
  metadata:
    name: ai-dash
  spec:
    tls:
      - hosts:
          - dash.ai-coordinator.io
        secretName: ai-dash-tls
    rules:
      - host: dash.ai-coordinator.io
        http:
          paths:
            - path: /
              pathType: Prefix
              backend:
                service:
                  name: ai-dash
                  port:
                    number: 8080
  ```

- [ ] **Step 4: Apply it**
  ```bash
  KUBECONFIG=~/.kube/ai-dash-k3s.yaml kubectl apply -f infra/k8s/ingress.yaml
  ```

- [ ] **Step 5: Verify HTTPS works directly against the origin** (bypassing DNS/Cloudflare, using SNI)
  ```bash
  curl -sk --resolve dash.ai-coordinator.io:443:$ORACLE_IP https://dash.ai-coordinator.io/ | head -c 200
  ```
  Expected: returns the app's HTML. `-k` is expected here (curl won't trust a Cloudflare Origin CA cert by default) — this step only proves TLS termination and routing work, not public trust (Cloudflare handles client-facing trust once Task 6 is live).

- [ ] **Step 6: Commit the Ingress manifest**
  ```bash
  git add infra/k8s/ingress.yaml
  git commit -m "Add Ingress with Cloudflare Origin CA TLS for dash.ai-coordinator.io"
  ```

---

### Task 6: Cloudflare cutover — DNS, WAF, retire the old Worker route

**Files:** None (Cloudflare dashboard only).

**Interfaces:**
- Consumes: `$ORACLE_IP` from Task 1; the already-existing Cloudflare zone for `dash.ai-coordinator.io` (per `README.md`'s "Domain" section).

- [ ] **Step 1: Update the DNS record**
  - Cloudflare dashboard > DNS > find the existing `dash` A/CNAME record (currently pointed at the Cloud Run Worker setup).
  - Change it to an A record: `dash` → `$ORACLE_IP`, proxy status **Proxied** (orange cloud).

- [ ] **Step 2: Set SSL/TLS mode to Full (strict)**
  - SSL/TLS > Overview > set mode to **Full (strict)** (validates the Origin CA cert from Task 5 — anything less either breaks or weakens TLS to the origin).

- [ ] **Step 3: Add a rate-limiting rule approximating the current Cloud Armor policy** (20 req/min/IP)
  - Security > WAF > Rate limiting rules > Create rule.
  - Match: all requests to `dash.ai-coordinator.io`.
  - Rate: 20 requests per 1 minute per IP; action: Block for 60s on breach.

- [ ] **Step 4: Retire the old Cloudflare Worker route** for this domain (Workers Routes > remove the route pointing at Cloud Run) — the Ingress now handles host-based routing directly, so the Host-header-rewrite Worker is no longer needed.

- [ ] **Step 5: Verify end-to-end over the real domain**
  ```bash
  curl -sI https://dash.ai-coordinator.io/ | head -5
  ```
  Expected: `HTTP/2 200`, valid TLS (no `-k` needed now — Cloudflare's edge cert is publicly trusted), and response headers showing Cloudflare (`cf-ray` present).

- [ ] **Step 6: Verify rate limiting**
  ```bash
  for i in $(seq 1 30); do curl -s -o /dev/null -w "%{http_code}\n" https://dash.ai-coordinator.io/; done
  ```
  Expected: mostly `200`, then `429` once the burst exceeds 20/min.

---

### Task 7: Restrict the origin firewall to Cloudflare's IP ranges

**Files:** None (OCI console/CLI only).

**Interfaces:**
- Consumes: `$ORACLE_IP`'s security list from Task 1.

- [ ] **Step 1: Fetch Cloudflare's published IP ranges**
  ```bash
  curl -s https://www.cloudflare.com/ips-v4 > cf-ips-v4.txt
  curl -s https://www.cloudflare.com/ips-v6 > cf-ips-v6.txt
  ```

- [ ] **Step 2: Replace the permissive 80/443 security-list rules from Task 1**
  - In the OCI Console security list, remove the `0.0.0.0/0` → 80/443 rules.
  - Add one ingress rule per CIDR in `cf-ips-v4.txt`/`cf-ips-v6.txt` for TCP 80 and 443.
  - Leave the 6443 (k3s API) and 22 (SSH) rules from Task 1 untouched.

- [ ] **Step 3: Verify the origin is unreachable directly**
  ```bash
  curl -sk --resolve dash.ai-coordinator.io:443:$ORACLE_IP --max-time 5 https://dash.ai-coordinator.io/
  ```
  Expected: connection timeout/refused (no longer reachable outside Cloudflare's ranges).

- [ ] **Step 4: Verify the real domain still works** (proving Cloudflare's IPs are correctly allowed)
  ```bash
  curl -sI https://dash.ai-coordinator.io/ | head -3
  ```
  Expected: `HTTP/2 200`, unchanged from Task 6.

---

### Task 8: Switch CI/CD to GHCR + k3s

**Files:**
- Modify: `.github/workflows/deploy.yml` (replace the `deploy` job's Cloud Build steps)

**Interfaces:**
- Consumes: Deployment `ai-dash` / container `app` (Task 3); GHCR package `ghcr.io/gromano-ltx/ai_dash` (Task 3).
- Produces: a `K3S_KUBECONFIG` GitHub Actions secret (base64-encoded `~/.kube/ai-dash-k3s.yaml` from Task 2) that subsequent CI runs use to deploy.

- [ ] **Step 1: Add the `K3S_KUBECONFIG` repo secret**
  ```bash
  base64 < ~/.kube/ai-dash-k3s.yaml | gh secret set K3S_KUBECONFIG --repo gromano-ltx/ai_dash
  ```

- [ ] **Step 2: Replace the `deploy` job in `.github/workflows/deploy.yml`**, keeping the existing `checks` job as-is:
  ```yaml
    deploy:
      needs: checks
      runs-on: ubuntu-slim
      permissions:
        contents: read
        packages: write
      steps:
        - uses: actions/checkout@v4

        - name: Log in to GHCR
          run: echo "${{ secrets.GITHUB_TOKEN }}" | docker login ghcr.io -u "${{ github.actor }}" --password-stdin

        - name: Build and push image
          run: |
            SHA=$(git rev-parse --short HEAD)
            docker build -t "ghcr.io/gromano-ltx/ai_dash:$SHA" .
            docker push "ghcr.io/gromano-ltx/ai_dash:$SHA"
            echo "SHA=$SHA" >> "$GITHUB_ENV"

        - name: Set up kubectl
          run: |
            curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
            chmod +x kubectl
            sudo mv kubectl /usr/local/bin/kubectl

        - name: Deploy to k3s
          run: |
            echo "${{ secrets.K3S_KUBECONFIG }}" | base64 -d > kubeconfig
            KUBECONFIG=kubeconfig kubectl set image deployment/ai-dash "app=ghcr.io/gromano-ltx/ai_dash:$SHA"
            KUBECONFIG=kubeconfig kubectl rollout status deployment/ai-dash --timeout=120s
            rm kubeconfig
  ```
  Also delete the now-unused `id-token: write` permission and the `google-github-actions/auth@v2` step, since Workload Identity Federation to GCP is no longer needed here.

- [ ] **Step 3: Verify by pushing a trivial commit**
  ```bash
  git commit --allow-empty -m "Trigger CI/CD verification"
  git push origin main
  gh run watch
  ```
  Expected: `checks` and `deploy` jobs both succeed; `kubectl get deployment ai-dash -o wide` (against `~/.kube/ai-dash-k3s.yaml`) shows the image tag matching the new commit's short SHA.

- [ ] **Step 4: Commit the workflow change**
  ```bash
  git add .github/workflows/deploy.yml
  git commit -m "Switch CI/CD deploy target from Cloud Build/Cloud Run to GHCR/k3s"
  ```

---

### Task 9: Decommission GCP, with a buffer before 2026-07-31

**Files:**
- Delete: `cloudbuild.yaml`
- Delete: `infra/main.tf`, `infra/cloud_armor.tf`, `infra/variables.tf`, `infra/outputs.tf` (or the whole `infra/` directory if nothing else lives there)

**Interfaces:** None (terminal task — nothing downstream depends on GCP resources after this).

- [ ] **Step 1: Confirm the new stack has been serving all production traffic successfully for at least 48 hours** with no rollback to GCP needed (Task 6/7 verification steps passing consistently, no user-facing errors reported).

- [ ] **Step 2: Tear down GCP infra via Terraform**
  ```bash
  cd infra
  terraform destroy
  ```
  Expected: plan shows deletion of the Cloud Run service, Cloud SQL instance (requires disabling `deletion_protection` first — see below), Cloud Armor policy/LB, Artifact Registry repo, and related IAM bindings.

- [ ] **Step 3: Disable Cloud SQL deletion protection first if `terraform destroy` fails on it**
  ```bash
  gcloud sql instances patch ai-dash-db --no-deletion-protection
  terraform destroy
  ```

- [ ] **Step 4: Delete the now-unused CI/CD and IaC files from the repo**
  ```bash
  git rm cloudbuild.yaml
  git rm -r infra/
  git commit -m "Remove GCP Terraform/Cloud Build config after migrating to Oracle + k3s"
  ```

- [ ] **Step 5: Verify nothing references the removed files**
  ```bash
  grep -rn "cloudbuild.yaml\|infra/main.tf\|gcloud builds" .github/ README.md
  ```
  Expected: no matches (confirms `.github/workflows/deploy.yml` from Task 8 no longer references any of it).

- [ ] **Step 6: Update `README.md`'s "Domain" section** to describe the new Cloudflare → Oracle/k3s path instead of the retired Cloudflare-Worker-to-Cloud-Run one.
  ```bash
  git add README.md
  git commit -m "Update README domain/architecture notes for Oracle + k3s"
  ```

---

## Self-Review Notes

- **Spec coverage:** every phase of the design doc's "Phased rollout" (1-7) maps to Tasks 1-9 here; "post-deadline hardening" (design phase 8) is correctly excluded per the design's own Non-goals/Out of scope.
- **Placeholder scan:** the only literal `REPLACE_WITH_SHA` is an intentional one-time manual substitution in Task 3 Step 3, called out explicitly as such, before Task 8 automates it — not a deferred TODO.
- **Type/name consistency:** Deployment name `ai-dash`, container name `app`, Service name `ai-dash`, Secret name `ai-dash-secrets`, TLS secret `ai-dash-tls` are used identically across Tasks 3, 4, 5, and 8.
- **6443 exposure:** intentionally left open in Task 1 (mutual-TLS client-cert auth is the real boundary, not network position) — flagged explicitly there and consistent with it being reachable from GitHub Actions' dynamic runner IPs in Task 8.
