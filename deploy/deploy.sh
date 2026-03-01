#!/usr/bin/env bash
# PriceSpy — Redeploy/update script
# Usage: sudo bash deploy/deploy.sh
set -euo pipefail

APP_DIR="/opt/pricespy"

echo "=== PriceSpy Deploy ==="

# ── Pull latest code ────────────────────────────────────────────────────
echo "[1/3] Updating code..."
cd "$APP_DIR"

if [ -d ".git" ]; then
    git pull --ff-only
else
    echo "  No git repo found. Use rsync to update files manually:"
    echo "  rsync -avz --exclude='.env' --exclude='data/' ./ $APP_DIR/"
    exit 1
fi

# ── Reinstall dependencies if requirements changed ──────────────────────
echo "[2/3] Checking dependencies..."
source venv/bin/activate
pip install --quiet -r requirements.txt
deactivate

chown -R pricespy:pricespy "$APP_DIR"

# ── Restart service ─────────────────────────────────────────────────────
echo "[3/3] Restarting service..."
systemctl restart pricespy

# Wait briefly and verify
sleep 2
if systemctl is-active --quiet pricespy; then
    echo ""
    echo "=== Deploy successful ==="
    # Health check
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health || echo "000")
    if [ "$HTTP_CODE" = "200" ]; then
        echo "  Health check: OK"
    else
        echo "  Health check: FAILED (HTTP $HTTP_CODE)"
        echo "  Check logs: sudo journalctl -u pricespy -n 20"
    fi
else
    echo ""
    echo "=== Deploy FAILED ==="
    echo "  Service is not running. Check logs:"
    echo "  sudo journalctl -u pricespy -n 30"
    exit 1
fi
