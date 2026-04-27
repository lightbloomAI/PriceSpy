from serpapi import GoogleSearch
from typing import List, Optional, Dict, Any
from urllib.parse import urlparse, parse_qs, unquote
from ..config import get_settings
from .country import detect_country, should_include_retailer
import re
import httpx
from bs4 import BeautifulSoup




def extract_direct_url(url: str) -> str:
    """
    Extract the direct retailer URL from a Google redirect URL.
    Google Shopping URLs often look like:
    - https://www.google.com/url?q=https://retailer.com/...&...
    - https://www.google.com/aclk?sa=...&adurl=https://retailer.com/...

    But NOT from Google search URLs like:
    - https://www.google.com/search?ibp=oshop&q=search+query (q is search query, not redirect)
    """
    if not url:
        return url

    parsed = urlparse(url)

    # Check if it's a Google redirect URL (but NOT a search page)
    if ('google.com' in parsed.netloc or 'google.' in parsed.netloc):
        # Skip Google search pages - q parameter there is the search query, not a URL
        if parsed.path in ['/search', '/shopping/product']:
            return url

        query_params = parse_qs(parsed.query)

        # Try common redirect parameter names
        for param in ['q', 'url', 'adurl', 'dest']:
            if param in query_params:
                extracted = query_params[param][0]
                # Only use if it looks like a URL (starts with http)
                if extracted.startswith('http'):
                    return unquote(extracted)

    return url


def search_direct_retailer_url(retailer: str, product_title: str, api_key: str) -> Optional[str]:
    """
    Use Google Search API to find direct retailer URL for a product.
    """
    try:
        retailer_lower = retailer.lower()

        # Check if retailer name already looks like a domain
        if '.' in retailer_lower:
            # It's already a domain like "sportano.de"
            domains = [retailer_lower]
        else:
            # Guess common domain patterns
            clean_name = retailer_lower.replace(' ', '').replace("'", "")
            domains = [
                f"{clean_name}.com",
                f"{clean_name}.de",
                f"{clean_name}.net",
                f"{clean_name}.eu",
            ]

        # Clean product title - extract key product words
        # Remove brand repetitions and take meaningful words
        title_clean = product_title.replace("'", "").replace('"', '')
        title_words = [w for w in title_clean.split()[:4] if len(w) > 2]
        search_title = ' '.join(title_words)

        # Try with site: operator for first domain
        if domains:
            search_query = f'site:{domains[0]} {search_title}'

            params = {
                "engine": "google",
                "q": search_query,
                "api_key": api_key,
                "num": 3,
            }

            search = GoogleSearch(params)
            results = search.get_dict()

            if 'error' not in results:
                organic = results.get("organic_results", [])
                for result in organic:
                    link = result.get("link")
                    if link and 'google.com' not in link:
                        return link

        # Fallback: search without site: but include retailer name
        search_query = f'"{retailer}" {search_title}'

        params = {
            "engine": "google",
            "q": search_query,
            "api_key": api_key,
            "num": 3,
        }

        search = GoogleSearch(params)
        results = search.get_dict()

        if 'error' not in results:
            organic = results.get("organic_results", [])
            for result in organic:
                link = result.get("link")
                # Check if link contains retailer name (fuzzy match)
                if link and 'google.com' not in link:
                    link_lower = link.lower()
                    retailer_key = retailer_lower.split('.')[0].replace(' ', '')
                    if retailer_key in link_lower:
                        return link

    except Exception as e:
        print(f"Error searching for {retailer} URL: {e}")

    return None


# Region configuration for SerpAPI
REGION_CONFIG = {
    "eu": {
        "gl": "de",  # Germany as main EU market
        "hl": "en",  # English language
        "currency": "EUR",
        "location": "Germany",
    },
    "worldwide": {
        "gl": "us",  # US for worldwide (largest market)
        "hl": "en",
        "currency": "USD",
        "location": "United States",
    },
    "hu": {
        "gl": "hu",  # Hungary specifically
        "hl": "hu",
        "currency": "HUF",
        "location": "Hungary",
    }
}


def extract_price(price_str: str) -> Optional[float]:
    """Extract numeric price from a string like '$99.99', '99,99 EUR', or '29 999 Ft'."""
    if not price_str:
        return None
    # Remove currency symbols and normalize
    cleaned = price_str.replace(',', '.').replace(' ', '')
    # Remove common currency symbols
    cleaned = re.sub(r'[€$£¥₹Ft]', '', cleaned)
    cleaned = re.sub(r'EUR|USD|GBP|HUF', '', cleaned)
    # Extract number
    numbers = re.findall(r'[\d]+\.?\d*', cleaned)
    if numbers:
        try:
            return float(numbers[0])
        except ValueError:
            return None
    return None


def matches_search_query(title: str, query: str) -> bool:
    """Check if a product title matches the key terms in the search query."""
    if not title or not query:
        return False

    title_lower = title.lower()
    query_lower = query.lower()

    # Common words to ignore
    ignore_words = {
        'the', 'a', 'an', 'and', 'or', 'for', 'with', 'in', 'on', 'of',
        'men', 'mens', "men's", 'women', 'womens', "women's",
        'flagship', 'premium', 'new', 'original', 'genuine', 'official',
    }

    # Descriptive words that are nice-to-have but not required
    descriptive_words = {
        'wireless', 'bluetooth', 'noise', 'cancelling', 'canceling',
        'over-ear', 'overear', 'on-ear', 'headphones', 'headphone',
        'earbuds', 'earphones', 'speaker', 'speakers',
        # Colors - optional variant attributes
        'black', 'white', 'blue', 'red', 'green', 'silver', 'gold', 'grey', 'gray',
        'midnight', 'platinum', 'pink', 'purple', 'orange', 'yellow', 'brown',
    }

    query_words = [w for w in query_lower.split() if w not in ignore_words and len(w) > 2]

    if not query_words:
        return True

    # Separate key identifiers (model numbers, brand) from descriptive words
    # Key words: contain digits OR are short (likely brand/model)
    key_words = []
    desc_words = []

    for word in query_words:
        word_clean = word.replace('-', '')
        if any(c.isdigit() for c in word_clean) or word_clean in descriptive_words:
            if word_clean not in descriptive_words:
                key_words.append(word)  # Model numbers like "wh-1000xm6"
            else:
                desc_words.append(word)
        elif len(word) <= 5:
            key_words.append(word)  # Short words like "sony"
        else:
            desc_words.append(word)

    # All key words (brand + model) must match
    key_matches = sum(1 for word in key_words if word in title_lower)
    if key_words and key_matches < len(key_words):
        return False

    # If all key identifiers matched (brand + model), that's good enough
    # Descriptive words are optional bonus matching
    if key_words and key_matches == len(key_words):
        return True

    # Fallback: require at least 25% of descriptive words to match
    if desc_words:
        desc_matches = sum(1 for word in desc_words if word in title_lower)
        min_required = max(1, int(len(desc_words) * 0.25))
        if desc_matches < min_required:
            return False

    return True


def search_google_shopping(
    query: str,
    region: str = "eu",
    size: Optional[str] = None,
    color: Optional[str] = None,
    brand: Optional[str] = None,
    model: Optional[str] = None,
    storage: Optional[str] = None,
    material: Optional[str] = None,
    max_results: int = 10
) -> List[dict]:
    """
    Search Google Shopping for product prices.

    Returns a list of dicts with: retailer, price, currency, url, title
    """
    settings = get_settings()

    if not settings.serpapi_key:
        raise ValueError("SERPAPI_KEY not configured")

    # Get region config
    region_cfg = REGION_CONFIG.get(region, REGION_CONFIG["eu"])

    # Simplify long queries to essential keywords (brand + model number)
    # Long queries often fail in Google Shopping
    query_words = query.split()
    skip_words = {
        'noise', 'cancelling', 'canceling', 'wireless', 'bluetooth',
        'black', 'white', 'blue', 'red', 'green', 'silver', 'gold', 'grey', 'gray',
        'midnight', 'platinum', 'pink', 'purple', 'over-ear', 'on-ear',
    }
    if len(query_words) > 5:
        # Extract key identifiers: words with digits (model numbers) and short brand words
        essential = []
        for word in query_words:
            word_lower = word.lower()
            word_clean = word.replace('-', '')
            # Skip common descriptive/color words
            if word_lower in skip_words:
                continue
            # Keep model numbers (contain digits) or very short words (brand)
            if any(c.isdigit() for c in word_clean) or len(word) <= 4:
                essential.append(word)
        if essential:
            query = ' '.join(essential[:3])  # Limit to 3 key terms

    # Build search query with variants (only add if not already in query)
    # Note: Don't add color - too restrictive for Google Shopping
    search_parts = [query]
    query_lower = query.lower()
    if brand and brand.lower() not in query_lower:
        search_parts.append(brand)
    if model and model.lower() not in query_lower:
        search_parts.append(model)
    if size:
        search_parts.append(size)
    # Note: color is intentionally not added - too restrictive for Google Shopping
    if storage:
        search_parts.append(storage)
    if material:
        search_parts.append(material)

    search_query = " ".join(search_parts)

    params = {
        "engine": "google_shopping",
        "q": search_query,
        "api_key": settings.serpapi_key,
        "num": max_results,
        "hl": region_cfg["hl"],
        "gl": region_cfg["gl"],
    }

    # Add location for more accurate results
    if "location" in region_cfg:
        params["location"] = region_cfg["location"]

    search = GoogleSearch(params)
    results = search.get_dict()

    prices = []
    currency = region_cfg["currency"]

    # Parse shopping results
    shopping_results = results.get("shopping_results", [])

    for item in shopping_results[:max_results * 3]:  # Check more results to find matches
        title = item.get("title", "")
        retailer = item.get("source", "Unknown")
        raw_url = item.get("product_link") or item.get("link", "")

        # Try to extract direct retailer URL from Google redirect
        url = extract_direct_url(raw_url)

        # Skip Amazon — anti-bot blocks our scraper from datacenter IPs and
        # the Keepa API workaround is paywalled. Filter at source so we never
        # add unscrapable rows to the DB.
        retailer_lower = (retailer or "").lower()
        url_lower = (url or "").lower()
        if "amazon" in retailer_lower or "amazon." in url_lower:
            continue

        # Skip items that don't match the search query
        if not matches_search_query(title, search_query):
            continue

        # Detect country and filter by region
        country_code, country_name = detect_country(retailer, url)
        if not should_include_retailer(country_code, region):
            continue

        price_str = item.get("price") or item.get("extracted_price")
        price = None

        # Try extracted_price first (numeric), then parse price string
        if "extracted_price" in item and item["extracted_price"]:
            price = float(item["extracted_price"])
        elif price_str:
            price = extract_price(price_str)

        if price is None:
            continue

        prices.append({
            "retailer": retailer,
            "price": price,
            "currency": currency,
            "url": url,
            "title": title,
            "thumbnail": item.get("thumbnail", ""),
            "country_code": country_code,
            "country_name": country_name,
        })

        if len(prices) >= max_results:
            break

    return prices


async def scrape_arukereso(search_query: str, max_results: int = 15) -> List[dict]:
    """
    Scrape prices from arukereso.hu (Hungarian price comparison site).
    Returns list of offers with retailer, price, currency, url.
    """
    from playwright.async_api import async_playwright

    # Simplify long queries to essential keywords (brand + model)
    query_words = search_query.split()
    skip_words = {
        'noise', 'cancelling', 'canceling', 'wireless', 'bluetooth',
        'black', 'white', 'blue', 'red', 'green', 'silver', 'gold',
        'midnight', 'platinum', 'over-ear', 'on-ear', 'headphones',
        'flagship', 'premium', 'new', 'original',
    }
    if len(query_words) > 4:
        essential = []
        for word in query_words:
            word_lower = word.lower()
            word_clean = word.replace('-', '')
            if word_lower in skip_words:
                continue
            if any(c.isdigit() for c in word_clean) or len(word) <= 4:
                essential.append(word)
        if essential:
            search_query = ' '.join(essential[:3])

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        )

        try:
            # Search for the product
            search_url = f'https://www.arukereso.hu/CategorySearch.php?st={search_query.replace(" ", "+")}'
            await page.goto(search_url, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)

            # Try to click on show all offers
            try:
                offers_btn = page.locator('a:has-text("ajánlat")').first
                if await offers_btn.is_visible(timeout=2000):
                    await offers_btn.click()
                    await page.wait_for_timeout(2000)
            except:
                pass

            html = await page.content()
            soup = BeautifulSoup(html, "lxml")

            offers = []

            # Parse optoffer elements
            for offer_div in soup.select(".optoffer"):
                # Get shop name from logo alt
                logo_img = offer_div.select_one('img.logo-host, img[alt*="ajánlatok"]')
                shop_name = None
                if logo_img:
                    shop_name = logo_img.get("alt", "").replace(" ajánlatok", "")

                if not shop_name:
                    continue

                # Get price
                price_elem = offer_div.select_one('.price-value, span[itemprop="price"]')
                if not price_elem:
                    price_meta = offer_div.select_one('meta[itemprop="price"]')
                    price_text = price_meta.get("content", "") if price_meta else ""
                else:
                    price_text = price_elem.get_text().strip()

                price_match = re.search(r"([\d\s,]+)", price_text.replace(" ", ""))
                if not price_match:
                    continue

                try:
                    price = int(price_match.group(1).replace(",", "").replace(" ", ""))
                    if price < 1000:  # Skip invalid prices
                        continue
                except:
                    continue

                # Get URL
                jump_link = offer_div.select_one("a.jumplink-overlay")
                url = jump_link.get("href", "") if jump_link else ""

                # Avoid duplicates
                if not any(o["retailer"] == shop_name and o["price"] == price for o in offers):
                    offers.append({
                        "retailer": shop_name,
                        "price": price,
                        "currency": "HUF",
                        "url": url,
                        "title": search_query,
                        "thumbnail": "",
                        "country_code": "HU",
                        "country_name": "Hungary",
                    })

                if len(offers) >= max_results:
                    break

        finally:
            await browser.close()

        # Sort by price
        offers.sort(key=lambda x: x["price"])
        return offers


async def scrape_product_prices(
    product_id: int,
    search_query: str,
    region: str = "eu",
    size: Optional[str] = None,
    color: Optional[str] = None,
    brand: Optional[str] = None,
    model: Optional[str] = None,
    storage: Optional[str] = None,
    material: Optional[str] = None,
) -> List[dict]:
    """
    Scrape prices for a product and return results.
    This is an async wrapper around the sync SerpAPI call.
    """
    import asyncio

    # For Hungarian region, also scrape arukereso.hu
    arukereso_prices = []
    if region == "hu":
        try:
            arukereso_prices = await scrape_arukereso(search_query)
        except Exception as e:
            print(f"Arukereso scraping failed: {e}")

    # Run sync SerpAPI call in thread pool
    loop = asyncio.get_event_loop()
    prices = await loop.run_in_executor(
        None,
        lambda: search_google_shopping(
            search_query,
            region=region,
            size=size,
            color=color,
            brand=brand,
            model=model,
            storage=storage,
            material=material,
        )
    )

    # Merge results, avoiding duplicates by retailer name
    existing_retailers = {p["retailer"].lower() for p in prices}
    for offer in arukereso_prices:
        if offer["retailer"].lower() not in existing_retailers:
            prices.append(offer)
            existing_retailers.add(offer["retailer"].lower())

    # Try to resolve direct retailer URLs using Google Search API
    # Limit to top 5 results to save API credits
    settings = get_settings()

    for i, price_data in enumerate(prices[:5]):
        url = price_data.get("url", "")
        if url and 'google.com' in url:
            retailer = price_data.get("retailer", "")
            title = price_data.get("title", "")

            if retailer and title:
                direct_url = await loop.run_in_executor(
                    None,
                    lambda r=retailer, t=title: search_direct_retailer_url(r, t, settings.serpapi_key)
                )
                if direct_url:
                    prices[i]["url"] = direct_url

    return prices
