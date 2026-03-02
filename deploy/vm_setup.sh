#!/usr/bin/env bash
# =============================================================================
# TenderScout AI — VM Setup Script
#
# Runs on the GCP VM. Installs Docker, starts services, sets up auto-restart.
# Called by gcp_deploy.sh — can also be run manually:
#   APP_DIR=/opt/tenderscout bash vm_setup.sh
# =============================================================================

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/tenderscout}"
LOG_FILE="/var/log/tenderscout_setup.log"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()    { echo -e "${BLUE}[INFO]${NC}  $*" | tee -a "$LOG_FILE"; }
success() { echo -e "${GREEN}[OK]${NC}    $*" | tee -a "$LOG_FILE"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*" | tee -a "$LOG_FILE"; }

sudo touch "$LOG_FILE"
sudo chmod 666 "$LOG_FILE"

info "=== TenderScout VM Setup Started ==="
info "App directory: $APP_DIR"

# ── System update ─────────────────────────────────────────────────────────────
info "Updating system packages..."
sudo apt-get update -y -q
sudo apt-get upgrade -y -q
sudo apt-get install -y -q \
  curl wget git unzip ca-certificates gnupg lsb-release \
  htop ncdu rsync openssl

# ── Install Docker ────────────────────────────────────────────────────────────
if command -v docker &>/dev/null; then
  info "Docker already installed: $(docker --version)"
else
  info "Installing Docker..."
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] \
    https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
  sudo apt-get update -y -q
  sudo apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-compose-plugin
  sudo systemctl enable docker
  sudo systemctl start docker
  sudo usermod -aG docker "$USER"
  success "Docker installed: $(docker --version)"
fi

# ── Docker Compose (standalone v2) ───────────────────────────────────────────
if docker compose version &>/dev/null; then
  info "Docker Compose plugin already available: $(docker compose version)"
else
  info "Installing Docker Compose plugin..."
  COMPOSE_VERSION=$(curl -s https://api.github.com/repos/docker/compose/releases/latest | grep '"tag_name"' | cut -d'"' -f4)
  sudo curl -SL "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-x86_64" \
    -o /usr/local/lib/docker/cli-plugins/docker-compose 2>/dev/null || \
  sudo mkdir -p /usr/local/lib/docker/cli-plugins && \
  sudo curl -SL "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-x86_64" \
    -o /usr/local/lib/docker/cli-plugins/docker-compose
  sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
  success "Docker Compose installed: $(docker compose version)"
fi

# ── Required directories ──────────────────────────────────────────────────────
info "Creating required directories..."
sudo mkdir -p \
  "${APP_DIR}/data/enrichment_cache" \
  "${APP_DIR}/data/bm25_index" \
  "${APP_DIR}/chroma_data" \
  "${APP_DIR}/checkpoints" \
  "${APP_DIR}/temp_ingest"

sudo chown -R "$USER":"$USER" "$APP_DIR"
success "Directories created"

# ── Load .env ─────────────────────────────────────────────────────────────────
if [[ -f "${APP_DIR}/.env" ]]; then
  set -a
  source "${APP_DIR}/.env"
  set +a
  info "Loaded .env"
else
  warn ".env file not found at ${APP_DIR}/.env — services may fail to start"
fi

# ── Pull Docker images (pre-pull for faster startup) ──────────────────────────
info "Pre-pulling Docker images..."
cd "$APP_DIR"
# Run docker commands with newgrp to pick up docker group if just added
sg docker -c "docker pull pgvector/pgvector:pg16 2>&1 | tail -1" || true
sg docker -c "docker pull redis:7-alpine 2>&1 | tail -1" || true
sg docker -c "docker pull chromadb/chroma:latest 2>&1 | tail -1" || true
success "Docker images pulled"

# ── Build and start services ──────────────────────────────────────────────────
info "Building and starting TenderScout services..."
cd "$APP_DIR"
sg docker -c "docker compose build --no-cache api"
sg docker -c "docker compose up -d"

info "Waiting for services to become healthy (up to 120s)..."
TIMEOUT=120
ELAPSED=0
while [[ $ELAPSED -lt $TIMEOUT ]]; do
  HEALTHY=$(sg docker -c "docker compose ps --format json" 2>/dev/null | \
    python3 -c "import sys,json; data=sys.stdin.read(); \
    rows=[json.loads(l) for l in data.strip().split('\n') if l]; \
    print(sum(1 for r in rows if r.get('Health','') in ('healthy','')))" 2>/dev/null || echo "0")
  TOTAL=$(sg docker -c "docker compose ps -q" 2>/dev/null | wc -l || echo "0")
  info "Services running: ${HEALTHY}/${TOTAL}..."
  if sg docker -c "docker compose ps api" 2>/dev/null | grep -q "Up"; then
    break
  fi
  sleep 5
  ELAPSED=$((ELAPSED + 5))
done
success "Services started"

# ── Health check ──────────────────────────────────────────────────────────────
info "Running health check..."
sleep 5  # Give API a moment to fully initialise
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health 2>/dev/null || echo "000")
if [[ "$HTTP_STATUS" == "200" ]]; then
  success "API health check passed (HTTP $HTTP_STATUS)"
  curl -s http://localhost:8000/health | python3 -m json.tool 2>/dev/null || true
else
  warn "API health check returned HTTP $HTTP_STATUS — check logs with: docker logs to_api"
fi

# ── systemd service for auto-restart on VM reboot ─────────────────────────────
info "Installing systemd service for auto-restart..."
sudo tee /etc/systemd/system/tenderscout.service > /dev/null <<SYSTEMD
[Unit]
Description=TenderScout AI
After=docker.service network-online.target
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=300
User=${USER}

[Install]
WantedBy=multi-user.target
SYSTEMD

sudo systemctl daemon-reload
sudo systemctl enable tenderscout
success "systemd service installed — TenderScout will auto-start on VM reboot"

# ── Log rotation ──────────────────────────────────────────────────────────────
sudo tee /etc/logrotate.d/tenderscout > /dev/null <<LOGROTATE
/var/log/tenderscout_setup.log {
    daily
    rotate 7
    compress
    missingok
    notifempty
}
LOGROTATE

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  VM Setup Complete                                    ${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════${NC}"
echo ""
echo "  API running at : http://$(curl -s ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}'):8000"
echo ""
echo "  Useful commands:"
echo "    View logs      : docker logs to_api -f"
echo "    Service status : docker compose ps  (from $APP_DIR)"
echo "    Restart        : sudo systemctl restart tenderscout"
echo "    Stop           : sudo systemctl stop tenderscout"
echo ""
