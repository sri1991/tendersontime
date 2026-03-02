#!/usr/bin/env bash
# =============================================================================
# TenderScout AI — GCP VM Deployment Script
#
# Usage:
#   chmod +x deploy/gcp_deploy.sh
#   ./deploy/gcp_deploy.sh \
#       --project   my-gcp-project \
#       --zone      asia-southeast1-b \
#       --vm-name   tenderscout-vm \
#       --api-key   "AIza..."
#
# Prerequisites:
#   - gcloud CLI installed and authenticated (gcloud auth login)
#   - gcloud project set (gcloud config set project YOUR_PROJECT)
#   - SSH key in ~/.ssh/id_rsa (or configure --ssh-key-file)
# =============================================================================

set -euo pipefail

# ── Defaults ──────────────────────────────────────────────────────────────────
GCP_PROJECT=""
GCP_ZONE="asia-southeast1-b"
GCP_REGION="asia-southeast1"
VM_NAME="tenderscout-vm"
MACHINE_TYPE="e2-standard-4"    # 4 vCPU, 16 GB RAM
BOOT_DISK_SIZE="60GB"
BOOT_DISK_TYPE="pd-ssd"
OS_IMAGE_FAMILY="ubuntu-2204-lts"
OS_IMAGE_PROJECT="ubuntu-os-cloud"
GEMINI_API_KEY=""
DB_PASSWORD="$(openssl rand -base64 16 | tr -d '/+=' | head -c 20)"
FIREWALL_RULE="allow-tenderscout-http"
APP_DIR="/opt/tenderscout"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# ── Argument parsing ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)   GCP_PROJECT="$2";   shift 2 ;;
    --zone)      GCP_ZONE="$2";      shift 2 ;;
    --vm-name)   VM_NAME="$2";       shift 2 ;;
    --api-key)   GEMINI_API_KEY="$2"; shift 2 ;;
    --machine)   MACHINE_TYPE="$2";  shift 2 ;;
    --db-pass)   DB_PASSWORD="$2";   shift 2 ;;
    *) error "Unknown argument: $1. See script header for usage." ;;
  esac
done

# ── Validation ────────────────────────────────────────────────────────────────
[[ -z "$GCP_PROJECT" ]]   && error "--project is required (your GCP project ID)"
[[ -z "$GEMINI_API_KEY" ]] && error "--api-key is required (your Gemini API key)"

command -v gcloud &>/dev/null || error "gcloud CLI not found. Install: https://cloud.google.com/sdk/docs/install"

info "Deployment configuration:"
echo "  GCP Project  : $GCP_PROJECT"
echo "  Zone         : $GCP_ZONE"
echo "  VM Name      : $VM_NAME"
echo "  Machine Type : $MACHINE_TYPE"
echo "  App Dir (VM) : $APP_DIR"
echo ""
read -rp "Proceed? [y/N] " confirm
[[ "$confirm" =~ ^[Yy]$ ]] || { info "Aborted."; exit 0; }

# ── Set active project ────────────────────────────────────────────────────────
gcloud config set project "$GCP_PROJECT" --quiet
success "GCP project set to $GCP_PROJECT"

# ── Create VM ─────────────────────────────────────────────────────────────────
info "Creating VM '$VM_NAME'..."

if gcloud compute instances describe "$VM_NAME" --zone="$GCP_ZONE" &>/dev/null; then
  warn "VM '$VM_NAME' already exists. Skipping creation."
else
  gcloud compute instances create "$VM_NAME" \
    --zone="$GCP_ZONE" \
    --machine-type="$MACHINE_TYPE" \
    --image-family="$OS_IMAGE_FAMILY" \
    --image-project="$OS_IMAGE_PROJECT" \
    --boot-disk-size="$BOOT_DISK_SIZE" \
    --boot-disk-type="$BOOT_DISK_TYPE" \
    --tags="tenderscout-api" \
    --metadata="enable-oslogin=TRUE" \
    --quiet
  success "VM created: $VM_NAME"
fi

# ── Reserve static external IP ────────────────────────────────────────────────
info "Reserving static IP for $VM_NAME..."
STATIC_IP_NAME="${VM_NAME}-ip"
if ! gcloud compute addresses describe "$STATIC_IP_NAME" --region="$GCP_REGION" &>/dev/null; then
  gcloud compute addresses create "$STATIC_IP_NAME" \
    --region="$GCP_REGION" \
    --quiet
  success "Static IP reserved: $STATIC_IP_NAME"

  # Assign the static IP to the VM's access config
  STATIC_IP=$(gcloud compute addresses describe "$STATIC_IP_NAME" --region="$GCP_REGION" --format="value(address)")
  gcloud compute instances delete-access-config "$VM_NAME" \
    --access-config-name="external-nat" \
    --zone="$GCP_ZONE" --quiet 2>/dev/null || true
  gcloud compute instances add-access-config "$VM_NAME" \
    --zone="$GCP_ZONE" \
    --address="$STATIC_IP" \
    --quiet
  success "Static IP $STATIC_IP assigned to $VM_NAME"
else
  STATIC_IP=$(gcloud compute addresses describe "$STATIC_IP_NAME" --region="$GCP_REGION" --format="value(address)")
  warn "Static IP already exists: $STATIC_IP"
fi

# ── Firewall rules ────────────────────────────────────────────────────────────
info "Setting up firewall rules..."

# Allow port 8000 (API)
if ! gcloud compute firewall-rules describe "$FIREWALL_RULE" &>/dev/null; then
  gcloud compute firewall-rules create "$FIREWALL_RULE" \
    --direction=INGRESS \
    --action=ALLOW \
    --rules=tcp:8000 \
    --source-ranges=0.0.0.0/0 \
    --target-tags=tenderscout-api \
    --description="Allow TenderScout API on port 8000" \
    --quiet
  success "Firewall rule created: allow TCP 8000"
else
  warn "Firewall rule already exists: $FIREWALL_RULE"
fi

# Block external access to internal services (postgres, redis, chroma)
for RULE_NAME in "deny-tenderscout-internal"; do
  if ! gcloud compute firewall-rules describe "$RULE_NAME" &>/dev/null; then
    gcloud compute firewall-rules create "$RULE_NAME" \
      --direction=INGRESS \
      --action=DENY \
      --rules=tcp:5432,tcp:6379 \
      --source-ranges=0.0.0.0/0 \
      --target-tags=tenderscout-api \
      --priority=900 \
      --description="Block external access to internal DB/cache ports" \
      --quiet
    success "Firewall rule created: deny external DB/cache access"
  fi
done

# ── Wait for VM SSH to be ready ───────────────────────────────────────────────
info "Waiting for VM SSH to become available..."
for i in {1..20}; do
  if gcloud compute ssh "$VM_NAME" --zone="$GCP_ZONE" --command="echo ready" --quiet 2>/dev/null; then
    success "SSH is ready"
    break
  fi
  echo "  Attempt $i/20 — waiting 10s..."
  sleep 10
done

# ── Copy project files to VM ──────────────────────────────────────────────────
info "Copying project files to VM..."

# Create app directory on VM
gcloud compute ssh "$VM_NAME" --zone="$GCP_ZONE" --quiet -- "sudo mkdir -p $APP_DIR && sudo chown \$USER:\$USER $APP_DIR"

# Rsync the project (excluding venv, chroma_db, __pycache__, data)
gcloud compute scp --recurse \
  --zone="$GCP_ZONE" \
  --exclude=".git,venv,__pycache__,*.pyc,chroma_db,temp_ingest,checkpoints" \
  "$REPO_ROOT/." \
  "${VM_NAME}:${APP_DIR}" \
  --quiet

success "Project files copied to ${VM_NAME}:${APP_DIR}"

# ── Write .env on VM ──────────────────────────────────────────────────────────
info "Writing .env file on VM..."
gcloud compute ssh "$VM_NAME" --zone="$GCP_ZONE" --quiet -- "cat > ${APP_DIR}/.env" <<EOF
GEMINI_API_KEY=${GEMINI_API_KEY}
DATABASE_URL=postgresql+asyncpg://tender:${DB_PASSWORD}@postgres:5432/tenderscout
REDIS_URL=redis://redis:6379/0
CHROMA_HOST=chroma
CHROMA_PORT=8000
SEARCH_BACKEND=postgres
POSTGRES_PASSWORD=${DB_PASSWORD}
EOF

# Also patch docker-compose to use the dynamic DB password
gcloud compute ssh "$VM_NAME" --zone="$GCP_ZONE" --quiet -- \
  "sed -i 's/POSTGRES_PASSWORD: tender/POSTGRES_PASSWORD: ${DB_PASSWORD}/' ${APP_DIR}/docker-compose.yml && \
   sed -i 's|postgresql+asyncpg://tender:tender@|postgresql+asyncpg://tender:${DB_PASSWORD}@|g' ${APP_DIR}/docker-compose.yml"

success ".env written with secure DB password"

# ── Run VM setup script ───────────────────────────────────────────────────────
info "Running VM setup script..."
gcloud compute scp \
  --zone="$GCP_ZONE" \
  "$REPO_ROOT/deploy/vm_setup.sh" \
  "${VM_NAME}:/tmp/vm_setup.sh" \
  --quiet

gcloud compute ssh "$VM_NAME" --zone="$GCP_ZONE" --quiet -- \
  "chmod +x /tmp/vm_setup.sh && APP_DIR=${APP_DIR} /tmp/vm_setup.sh"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  TenderScout AI deployed successfully!                ${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════${NC}"
echo ""
echo "  VM Name    : $VM_NAME"
echo "  Public IP  : $STATIC_IP"
echo "  API URL    : http://${STATIC_IP}:8000"
echo "  Health     : http://${STATIC_IP}:8000/health"
echo "  DLQ        : http://${STATIC_IP}:8000/api/dlq"
echo ""
echo "  SSH access : gcloud compute ssh $VM_NAME --zone=$GCP_ZONE"
echo ""
echo -e "${YELLOW}  Next step — run ChromaDB migration (if you have existing data):${NC}"
echo "  gcloud compute ssh $VM_NAME --zone=$GCP_ZONE -- \\"
echo "    \"cd ${APP_DIR} && source .env && docker exec to_api python scripts/migrate_chroma_to_postgres.py\""
echo ""
