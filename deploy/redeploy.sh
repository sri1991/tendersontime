#!/usr/bin/env bash
# =============================================================================
# TenderScout AI — Re-deploy to existing GCP VM
#
# Use this after code changes — syncs files and restarts services.
# Much faster than a full gcp_deploy.sh run.
#
# Usage:
#   ./deploy/redeploy.sh --vm-name tenderscout-vm --zone asia-southeast1-b
# =============================================================================

set -euo pipefail

VM_NAME="tenderscout-vm"
GCP_ZONE="asia-southeast1-b"
APP_DIR="/opt/tenderscout"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

RED='\033[0;31m'; GREEN='\033[0;32m'; BLUE='\033[0;34m'; NC='\033[0m'
info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --vm-name) VM_NAME="$2"; shift 2 ;;
    --zone)    GCP_ZONE="$2"; shift 2 ;;
    --app-dir) APP_DIR="$2"; shift 2 ;;
    *) error "Unknown argument: $1" ;;
  esac
done

command -v gcloud &>/dev/null || error "gcloud CLI not found"

info "Re-deploying to $VM_NAME ($GCP_ZONE)..."

# ── Sync code (exclude data, env, caches) ────────────────────────────────────
info "Syncing code files..."
gcloud compute scp --recurse \
  --zone="$GCP_ZONE" \
  --exclude=".git,venv,__pycache__,*.pyc,chroma_db,temp_ingest,checkpoints,.env,data" \
  "$REPO_ROOT/." \
  "${VM_NAME}:${APP_DIR}" \
  --quiet
success "Code synced"

# ── Rebuild API image and restart ────────────────────────────────────────────
info "Rebuilding API container and restarting..."
gcloud compute ssh "$VM_NAME" --zone="$GCP_ZONE" --quiet -- \
  "cd ${APP_DIR} && \
   sg docker -c 'docker compose build --no-cache api' && \
   sg docker -c 'docker compose up -d --no-deps api'"

# ── Health check ─────────────────────────────────────────────────────────────
info "Waiting for API to be ready..."
sleep 8
PUBLIC_IP=$(gcloud compute instances describe "$VM_NAME" \
  --zone="$GCP_ZONE" \
  --format="value(networkInterfaces[0].accessConfigs[0].natIP)" 2>/dev/null || echo "unknown")

HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "http://${PUBLIC_IP}:8000/health" 2>/dev/null || echo "000")
if [[ "$HTTP_STATUS" == "200" ]]; then
  success "API healthy at http://${PUBLIC_IP}:8000 (HTTP $HTTP_STATUS)"
else
  echo "  Health check returned HTTP $HTTP_STATUS"
  echo "  Check logs: gcloud compute ssh $VM_NAME --zone=$GCP_ZONE -- 'docker logs to_api --tail 50'"
fi

echo ""
success "Re-deploy complete → http://${PUBLIC_IP}:8000"
