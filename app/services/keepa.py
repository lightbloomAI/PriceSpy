"""
Keepa API integration for Amazon product data.

Why this exists: scraping amazon.* directly from datacenter IPs (Hetzner,
Render, etc.) gets anti-bot blocked — the buy-box block is stripped from
the HTML so price selectors all return empty. Keepa is purpose-built for
this and returns live prices via API.

Docs: https://keepa.com/#!discuss/t/product-object/116
Pricing: https://keepa.com/#!api  (free tier ~60 tokens/min)
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse

import httpx

from app.config import get_settings

KEEPA_BASE_URL = "https://api.keepa.com"

# Keepa domain IDs — see https://keepa.com/#!discuss/t/product-finder/41
DOMAIN_MAP = {
    "amazon.com": 1,
    "amazon.co.uk": 2,
    "amazon.de": 3,
    "amazon.fr": 4,
    "amazon.co.jp": 5,
    "amazon.ca": 6,
    "amazon.it": 8,
    "amazon.es": 9,
    "amazon.in": 10,
    "amazon.com.mx": 11,
    "amazon.com.br": 12,
    "amazon.nl": 13,
    "amazon.com.au": 14,
    "amazon.se": 15,
    "amazon.pl": 16,
    "amazon.com.tr": 17,
    "amazon.ae": 18,
    "amazon.sa": 19,
    "amazon.sg": 20,
    "amazon.com.be": 21,
}

CURRENCY_BY_DOMAIN = {
    1: "USD", 2: "GBP", 3: "EUR", 4: "EUR", 5: "JPY", 6: "CAD",
    8: "EUR", 9: "EUR", 10: "INR", 11: "MXN", 12: "BRL", 13: "EUR",
    14: "AUD", 15: "SEK", 16: "PLN", 17: "TRY", 18: "AED", 19: "SAR",
    20: "SGD", 21: "EUR",
}


def is_amazon_url(url: str) -> bool:
    return bool(re.search(r"\bamazon\.", urlparse(url).netloc.lower()))


def extract_asin(url: str) -> Optional[str]:
    """
    Pull the 10-character ASIN out of an Amazon URL.

    Matches /dp/ASIN, /gp/product/ASIN, /-/<lang>/<slug>/dp/ASIN, etc.
    """
    m = re.search(r"/(?:dp|gp/product|gp/aw/d|product)/([A-Z0-9]{10})(?:[/?]|$)", url, re.I)
    return m.group(1).upper() if m else None


def domain_id_for_url(url: str) -> Optional[int]:
    netloc = urlparse(url).netloc.lower().lstrip("www.")
    for host, did in DOMAIN_MAP.items():
        if netloc.endswith(host):
            return did
    return None


async def fetch_amazon_product(url: str) -> Optional[dict]:
    """
    Fetch product data from Keepa for the Amazon URL.

    Returns a dict with `price`, `currency`, `name`, `brand`, `images`, or
    None if the API key is missing / lookup fails / no price available.
    """
    settings = get_settings()
    api_key = settings.keepa_api_key
    if not api_key:
        return None

    asin = extract_asin(url)
    domain = domain_id_for_url(url)
    if not asin or not domain:
        return None

    params = {
        "key": api_key,
        "domain": domain,
        "asin": asin,
        # stats=1 → include parsed current/avg price stats so we don't have to
        # decode the raw price-history CSV ourselves
        "stats": 1,
        # offers=20 → include current third-party offers (helps when Amazon
        # itself isn't the seller)
        "offers": 20,
        "history": 0,
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            resp = await client.get(f"{KEEPA_BASE_URL}/product", params=params)
            resp.raise_for_status()
        except httpx.HTTPError:
            return None
        data = resp.json()

    products = data.get("products") or []
    if not products:
        return None
    product = products[0]

    price_cents = _pick_current_price_cents(product)
    if price_cents is None:
        return None

    currency = CURRENCY_BY_DOMAIN.get(domain, "EUR")
    images = _build_image_urls(product)

    return {
        "price": price_cents / 100.0,
        "currency": currency,
        "name": product.get("title"),
        "brand": product.get("brand") or product.get("manufacturer"),
        "images": images,
        "asin": asin,
    }


def _pick_current_price_cents(product: dict) -> Optional[int]:
    """
    Pick the most representative *current* price from a Keepa product object.

    Keepa records prices in cents; -1 means "no data". Preference order:
      1. stats.current[0]   — Amazon's own price (when sold by Amazon)
      2. stats.current[1]   — New marketplace (third-party new)
      3. stats.current[18]  — Buy Box price
      4. stats.current[3]   — Used
    """
    stats = product.get("stats") or {}
    current = stats.get("current") or []
    # Index meaning: 0=Amazon, 1=New, 2=Used, 3=Sales rank, ..., 18=Buy Box
    for idx in (0, 1, 18, 3):
        if idx < len(current):
            val = current[idx]
            if isinstance(val, (int, float)) and val > 0:
                return int(val)
    return None


def _build_image_urls(product: dict) -> list:
    """Keepa returns image filenames; expand them to full CDN URLs."""
    images_csv = product.get("imagesCSV") or ""
    if not images_csv:
        return []
    return [
        f"https://m.media-amazon.com/images/I/{name.strip()}"
        for name in images_csv.split(",")
        if name.strip()
    ][:10]
