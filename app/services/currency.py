import httpx
from typing import Optional
from functools import lru_cache
import time

# Cache exchange rates for 1 hour
_rate_cache: dict = {}
_cache_ttl = 3600  # 1 hour


async def get_exchange_rate(from_currency: str, to_currency: str) -> Optional[float]:
    """
    Get exchange rate from xe.com.
    Returns the rate to convert from_currency to to_currency.
    """
    if from_currency == to_currency:
        return 1.0

    cache_key = f"{from_currency}_{to_currency}"
    now = time.time()

    # Check cache
    if cache_key in _rate_cache:
        rate, cached_at = _rate_cache[cache_key]
        if now - cached_at < _cache_ttl:
            return rate

    try:
        # Use xe.com's conversion page
        url = f"https://www.xe.com/currencyconverter/convert/?Amount=1&From={from_currency}&To={to_currency}"

        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            }, follow_redirects=True)

            if response.status_code != 200:
                return None

            html = response.text

            # Parse the rate from the page - look for the result
            # XE shows "1 EUR = X HUF" in a specific format
            import re

            # Try to find the rate in the page
            # Pattern matches numbers like "411.123456" or "0.002432"
            pattern = rf'1\s*{from_currency}\s*=\s*([\d,]+\.?\d*)\s*{to_currency}'
            match = re.search(pattern, html, re.IGNORECASE)

            if match:
                rate_str = match.group(1).replace(',', '')
                rate = float(rate_str)
                _rate_cache[cache_key] = (rate, now)
                return rate

            # Alternative: look for data-rate attribute or similar
            # Try finding rate in a more flexible way
            pattern2 = rf'([\d,]+\.?\d+)\s*{to_currency}'
            matches = re.findall(pattern2, html)
            if matches:
                # Find a reasonable rate (not 1, not too small/large)
                for m in matches:
                    try:
                        rate = float(m.replace(',', ''))
                        if 0.0001 < rate < 10000000:
                            _rate_cache[cache_key] = (rate, now)
                            return rate
                    except ValueError:
                        continue

            return None

    except Exception as e:
        print(f"Error fetching exchange rate: {e}")
        return None


async def convert_price(amount: float, from_currency: str, to_currency: str) -> Optional[float]:
    """Convert a price from one currency to another."""
    if from_currency == to_currency:
        return amount

    rate = await get_exchange_rate(from_currency, to_currency)
    if rate is None:
        return None

    return amount * rate
