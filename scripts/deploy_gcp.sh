#!/bin/bash
# TenderScout GCP VM deployment script
# Run once on a fresh Ubuntu 22.04 VM:
#   chmod +x scripts/deploy_gcp.sh && ./scripts/deploy_gcp.sh

set -e

echo "==> Installing Docker..."
apt-get update -q
apt-get install -y ca-certificates curl gnupg nginx
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update -q
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
systemctl enable --now docker

echo "==> Cloning repo..."
cd /opt
git clone https://github.com/sri1991/tendersontime.git tenderscout
cd tenderscout

echo "==> Writing .env (edit this with your real key)..."
cat > .env <<'EOF'
GEMINI_API_KEY=YOUR_KEY_HERE
SEARCH_BACKEND=postgres
EOF

echo "==> Configuring nginx..."
cp nginx/nginx.conf /etc/nginx/sites-available/tenderscout
ln -sf /etc/nginx/sites-available/tenderscout /etc/nginx/sites-enabled/tenderscout
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo "==> Starting containers..."
docker compose up -d --build

echo "==> Waiting for postgres to be healthy..."
until docker exec to_postgres pg_isready -U tender -d tenderscout; do sleep 3; done

echo "==> Running migrations..."
docker cp src/db/migrations/002_fts_and_feedback.sql to_postgres:/tmp/002.sql
docker exec to_postgres psql -U tender -d tenderscout -f /tmp/002.sql

echo ""
echo "Done. TenderScout is running at http://$(curl -s ifconfig.me)"
echo "Edit /opt/tenderscout/.env and set your GEMINI_API_KEY, then: docker compose restart api ingest_worker"
