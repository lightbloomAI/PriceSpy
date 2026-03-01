#!/usr/bin/env bash
# PriceSpy — Server-side price refresh (called by cron every 2 hours)
# Refreshes prices for all active products by calling the API endpoint.
set -euo pipefail

BASE_URL="http://127.0.0.1:8000"
LOG_PREFIX="[$(date '+%Y-%m-%d %H:%M:%S')]"

# Load CRON_SECRET from .env (same directory or /opt/pricespy)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"
[ ! -f "$ENV_FILE" ] && ENV_FILE="${SCRIPT_DIR}/../.env"
if [ -f "$ENV_FILE" ]; then
    CRON_SECRET=$(grep -E '^CRON_SECRET=' "$ENV_FILE" | cut -d= -f2-)
fi
CRON_SECRET="${CRON_SECRET:-}"

if [ -z "$CRON_SECRET" ]; then
    echo "$LOG_PREFIX ERROR: CRON_SECRET not set in .env"
    exit 1
fi

AUTH_HEADER="Authorization: Bearer $CRON_SECRET"

echo "$LOG_PREFIX Starting price refresh..."

# Get all product IDs via the API
PRODUCT_IDS=$(curl -s -H "$AUTH_HEADER" "$BASE_URL/api/products" | grep -oP '"id"\s*:\s*\K[0-9]+' | sort -un)

if [ -z "$PRODUCT_IDS" ]; then
    echo "$LOG_PREFIX No products found."
    exit 0
fi

COUNT=0
FAILED=0

for ID in $PRODUCT_IDS; do
    RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" \
        -H "$AUTH_HEADER" \
        -X POST "$BASE_URL/api/product/$ID/refresh-prices")

    if [ "$RESPONSE" = "200" ]; then
        COUNT=$((COUNT + 1))
    else
        FAILED=$((FAILED + 1))
        echo "$LOG_PREFIX  Product $ID: refresh failed (HTTP $RESPONSE)"
    fi

    # Small delay to avoid hammering sites
    sleep 5
done

echo "$LOG_PREFIX Refresh complete: $COUNT succeeded, $FAILED failed."
