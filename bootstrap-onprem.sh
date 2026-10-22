#!/usr/bin/env bash
# ── Tiresias On-Prem Bootstrap (Twin-A Tenant) ──────────────────────────────
# Sources secrets from saluca-deploy/.env + GCP Secret Manager.
# Generates fresh .env for tiresias-core and app-proxy.
# Run: bash bootstrap-onprem.sh
set -euo pipefail

PROJECT="salucainfrastructure"
DEPLOY_ENV="/c/saluca-deploy/.env"
CORE_ENV="C:/tiresias-core/.env"
APP_PROXY_DIR="C:/app-proxy"
APP_PROXY_ENV="${APP_PROXY_DIR}/.env"

echo "=== Tiresias On-Prem Bootstrap (Fresh Start) ==="

# ── Step 0: Validate source env and extract needed keys ─────────────────────
if [[ ! -f "${DEPLOY_ENV}" ]]; then
  echo "ERROR: ${DEPLOY_ENV} not found"; exit 1
fi

# Extract only the specific keys we need (avoids sourcing vars with $)
extract_key() { grep "^$1=" "${DEPLOY_ENV}" | head -1 | cut -d'=' -f2-; }
ANTHROPIC_API_KEY=$(extract_key ANTHROPIC_API_KEY)
SOUL_API_KEY=$(extract_key SOUL_API_KEY)
SOUL_API_URL=$(extract_key SOUL_API_URL)
SLACK_BOT_TOKEN=$(extract_key SLACK_BOT_TOKEN)
SLACK_APP_TOKEN=$(extract_key SLACK_APP_TOKEN)
echo "[0] Extracted keys from ${DEPLOY_ENV}"

# ── Step 1: Clean local state ───────────────────────────────────────────────
echo "[1] Cleaning local state..."
rm -f "${CORE_ENV}" 2>/dev/null || true
rm -f "${APP_PROXY_ENV}" 2>/dev/null || true
rm -f "${APP_PROXY_DIR}/app_proxy.db" 2>/dev/null || true
docker compose -f C:/tiresias-core/docker-compose.yml down -v 2>/dev/null || true
docker compose -f "${APP_PROXY_DIR}/docker-compose.yml" down -v 2>/dev/null || true
echo "  Clean."

# ── Step 2: Generate fresh keys ─────────────────────────────────────────────
echo "[2] Generating encryption keys..."
FRESH_KEK=$(python -c "import secrets; print(secrets.token_hex(32))")
FRESH_APP_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")
FRESH_APP_KEY_HASH=$(python -c "import hashlib; print(hashlib.sha256('${FRESH_APP_KEY}'.encode()).hexdigest())")

# ── Step 3: Pull Tiresias-specific secrets from GCP ─────────────────────────
echo "[3] Pulling Tiresias secrets from GCP..."
fetch_secret() {
  gcloud secrets versions access latest --secret="$1" --project="${PROJECT}" 2>/dev/null || echo ""
}
LICENSE_TOKEN=$(fetch_secret "tiresias-license-key")
INTERNAL_KEY=$(fetch_secret "tiresias-internal-api-key")

# ── Step 4: Write tiresias-core .env ────────────────────────────────────────
echo "[4] Writing tiresias-core .env..."
cat > "${CORE_ENV}" <<ENVEOF
# Tiresias On-Prem (Twin-A Tenant)
# Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)

# License
TIRESIAS_LICENSE_TOKEN=${LICENSE_TOKEN}
TIRESIAS_MODE=onprem

# Ports
PROXY_PORT=8080
DASHBOARD_PORT=3000
RELAY_PORT=8090

# Providers
TIRESIAS_PROVIDERS=anthropic,ollama
ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
OLLAMA_BASE_URL=http://host.docker.internal:11434

# Encryption
ENCRYPTION_KEY=${FRESH_KEK}
TIRESIAS_KEK_PROVIDER=local
TIRESIAS_KEK=${FRESH_KEK}

# Storage (local SQLite, fresh)
DATABASE_URL=sqlite+aiosqlite:////app/data/tiresias.db

# Retention
TIRESIAS_RETENTION_DAYS=90
TIRESIAS_USAGE_RETENTION_DAYS=180

# Internal
TIRESIAS_INTERNAL_API_KEY=${INTERNAL_KEY}
SOUL_API_URL=${SOUL_API_URL:-}
SOUL_API_KEY=${SOUL_API_KEY:-}

# Logging
LOG_LEVEL=info
ENVEOF
echo "  Written: ${CORE_ENV}"

# ── Step 5: Write app-proxy .env ────────────────────────────────────────────
echo "[5] Writing app-proxy .env..."
cat > "${APP_PROXY_ENV}" <<ENVEOF
# App-Proxy On-Prem (Twin-A Tenant)
# Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)

# Core
APP_PROXY_PROXY_PORT=8081
APP_PROXY_POLICY_ENFORCEMENT_MODE=strict
APP_PROXY_DATABASE_URL=sqlite+aiosqlite:////app/data/app_proxy.db
APP_PROXY_POLICIES_DIR=/app/policies/cedar
APP_PROXY_CEDAR_SCHEMA_PATH=/app/policies/cedar_schema.json

# Auth
APP_PROXY_API_KEY_HASH=${FRESH_APP_KEY_HASH}
APP_PROXY_ADMIN_KEY=${FRESH_APP_KEY}

# Slack (optional, for approval notifications)
SLACK_BOT_TOKEN=${SLACK_BOT_TOKEN:-}
SLACK_APP_TOKEN=${SLACK_APP_TOKEN:-}

# Approval
APP_PROXY_ENABLE_APPROVAL_QUEUE=true
APP_PROXY_APPROVAL_TIMEOUT_MINUTES=30
APP_PROXY_RETENTION_DAYS=90
ENVEOF
echo "  Written: ${APP_PROXY_ENV}"

# ── Step 6: Summary ─────────────────────────────────────────────────────────
echo ""
echo "=== Bootstrap Complete ==="
echo "tiresias-core: $(grep -c '=' "${CORE_ENV}") vars"
echo "app-proxy:     $(grep -c '=' "${APP_PROXY_ENV}") vars"
echo ""
echo "App-Proxy bearer token (save this):"
echo "  ${FRESH_APP_KEY}"
echo ""
echo "Next:"
echo "  1. Start Docker Desktop"
echo "  2. cd C:/tiresias-core && docker compose up -d --build"
echo "  3. cd C:/app-proxy && docker compose up -d --build"
echo "  4. curl http://localhost:8080/health"
echo "  5. curl http://localhost:8081/health"
