#!/usr/bin/env bash
# PriceSpy — One-time server setup (Ubuntu 22.04+ / Hetzner / Oracle Cloud)
# Usage: sudo bash deploy/setup.sh
set -euo pipefail

APP_DIR="/opt/pricespy"
APP_USER="pricespy"
REPO_URL="https://github.com/attilarepka/PriceSpy.git"  # Change if needed

echo "=== PriceSpy Server Setup ==="

# ── 1. System packages ──────────────────────────────────────────────────
echo "[1/8] Installing system packages..."
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git curl \
    debian-keyring debian-archive-keyring apt-transport-https

# ── 2. Install Caddy ────────────────────────────────────────────────────
echo "[2/8] Installing Caddy..."
if ! command -v caddy &>/dev/null; then
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | \
        gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | \
        tee /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -qq
    apt-get install -y -qq caddy
fi

# ── 3. Create app user and directory ────────────────────────────────────
echo "[3/8] Setting up app user and directory..."
if ! id "$APP_USER" &>/dev/null; then
    useradd --system --shell /usr/sbin/nologin --home-dir "$APP_DIR" "$APP_USER"
fi
mkdir -p "$APP_DIR" "$APP_DIR/data"

# ── 4. Clone or update code ────────────────────────────────────────────
echo "[4/8] Fetching code..."
if [ -d "$APP_DIR/.git" ]; then
    cd "$APP_DIR" && git pull --ff-only
else
    # If files were rsynced instead of cloned, skip git clone
    if [ -f "$APP_DIR/requirements.txt" ]; then
        echo "  Code already present (rsynced). Skipping git clone."
    else
        git clone "$REPO_URL" "$APP_DIR"
    fi
fi
chown -R "$APP_USER":"$APP_USER" "$APP_DIR"

# ── 5. Python venv + dependencies ──────────────────────────────────────
echo "[5/8] Setting up Python environment..."
cd "$APP_DIR"
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
source venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

# Install Playwright Chromium + system deps
echo "  Installing Playwright browser..."
playwright install --with-deps chromium
deactivate

chown -R "$APP_USER":"$APP_USER" "$APP_DIR"

# ── 6. Environment variables ───────────────────────────────────────────
echo "[6/8] Configuring environment..."
if [ ! -f "$APP_DIR/.env" ]; then
    echo ""
    echo "  Enter your credentials (stored in $APP_DIR/.env):"
    echo ""
    read -rp "  SERPAPI_KEY: " SERPAPI_KEY
    read -rp "  SMTP_USER (Gmail address): " SMTP_USER
    read -rsp "  SMTP_PASSWORD (Gmail app password): " SMTP_PASSWORD
    echo ""

    echo ""
    echo "  Login credentials for the web UI:"
    read -rp "  AUTH_EMAIL: " AUTH_EMAIL
    read -rsp "  AUTH_PASSWORD: " AUTH_PASSWORD
    echo ""

    # Generate bcrypt hash, SECRET_KEY, and CRON_SECRET
    source "$APP_DIR/venv/bin/activate"
    AUTH_PASSWORD_HASH=$(python3 -c "import bcrypt; print(bcrypt.hashpw(b'''${AUTH_PASSWORD}''', bcrypt.gensalt()).decode())")
    SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
    CRON_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
    deactivate

    cat > "$APP_DIR/.env" <<ENVEOF
SERPAPI_KEY=${SERPAPI_KEY}
SMTP_USER=${SMTP_USER}
SMTP_PASSWORD=${SMTP_PASSWORD}
DATABASE_PATH=/opt/pricespy/data/pricespy.db
SECRET_KEY=${SECRET_KEY}
CRON_SECRET=${CRON_SECRET}
AUTH_EMAIL=${AUTH_EMAIL}
AUTH_PASSWORD_HASH=${AUTH_PASSWORD_HASH}
ENVEOF

    chmod 600 "$APP_DIR/.env"
    chown "$APP_USER":"$APP_USER" "$APP_DIR/.env"
    echo "  .env created."
else
    echo "  .env already exists, skipping."
fi

# ── 7. Install systemd service + Caddy config ──────────────────────────
echo "[7/8] Installing systemd service and Caddy config..."
cp "$APP_DIR/deploy/pricespy.service" /etc/systemd/system/pricespy.service
systemctl daemon-reload
systemctl enable pricespy

cp "$APP_DIR/deploy/Caddyfile" /etc/caddy/Caddyfile

# ── 8. Install cron job ────────────────────────────────────────────────
echo "[8/8] Setting up cron job for price refreshes..."
cp "$APP_DIR/deploy/refresh_cron.sh" "$APP_DIR/refresh_cron.sh"
chmod +x "$APP_DIR/refresh_cron.sh"
chown "$APP_USER":"$APP_USER" "$APP_DIR/refresh_cron.sh"

# Add cron entry (every 2 hours) if not already present
CRON_LINE="0 */2 * * * $APP_DIR/refresh_cron.sh >> $APP_DIR/data/refresh.log 2>&1"
(crontab -u "$APP_USER" -l 2>/dev/null || true; echo "$CRON_LINE") | \
    sort -u | crontab -u "$APP_USER" -

# ── Start everything ───────────────────────────────────────────────────
echo ""
echo "=== Starting services ==="
systemctl restart pricespy
systemctl restart caddy

echo ""
echo "=== Setup complete! ==="
echo ""
echo "  App:      http://$(hostname -I | awk '{print $1}')"
echo "  Status:   sudo systemctl status pricespy"
echo "  Logs:     sudo journalctl -u pricespy -f"
echo "  Cron log: tail -f $APP_DIR/data/refresh.log"
echo ""
echo "  Next step (optional):"
echo "  Point a domain to this IP and update /etc/caddy/Caddyfile"
echo "  Then run: sudo systemctl restart caddy"
