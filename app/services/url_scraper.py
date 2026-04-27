"""
URL scraping service to extract product information from product pages.
"""
import httpx
import re
import json
from bs4 import BeautifulSoup
from typing import Optional, List, Dict, Any
from urllib.parse import urlparse

# Sites that require browser rendering
BROWSER_REQUIRED_DOMAINS = [
    'patagonia.com',
    'eu.patagonia.com',
    'polarnopyret.com',
    'zalando.',  # Zalando loads prices via JS
    'bergfreunde.',  # Bergfreunde loads prices via JS
    'queens.hu',
    'oliunid.com',
    'pipeline.pt',
    'mountex.hu',
    'sportano.',  # Sportano loads prices via JS
    'amazon.',  # Amazon has anti-bot measures
]


def needs_browser(url: str) -> bool:
    """Check if URL requires browser rendering."""
    domain = urlparse(url).netloc.lower()
    return any(d in domain for d in BROWSER_REQUIRED_DOMAINS)


async def scrape_with_browser(url: str) -> str:
    """Scrape URL using Playwright headless browser with stealth mode."""
    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth

    stealth = Stealth(
        navigator_platform_override="MacIntel",
        navigator_vendor_override="Google Inc.",
    )

    parsed_url = urlparse(url)
    domain = parsed_url.netloc.lower()

    async with stealth.use_async(async_playwright()) as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ]
        )

        # Set up context options - use site-specific locale
        locale = "en-GB"
        if 'amazon.de' in domain:
            locale = "de-DE"  # German locale for EUR prices
        elif 'amazon.com' in domain:
            locale = "en-US"

        context_options = {
            "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "viewport": {"width": 1920, "height": 1080},
            "locale": locale,
            "java_script_enabled": True,
        }

        # Add site-specific headers
        if 'polarnopyret.com' in domain:
            context_options["extra_http_headers"] = {
                "Accept-Language": "en-GB,en;q=0.9",
            }
        elif 'amazon.de' in domain:
            context_options["extra_http_headers"] = {
                "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
            }

        context = await browser.new_context(**context_options)

        # Set site-specific cookies before navigation
        if 'amazon.de' in domain:
            await context.add_cookies([
                {
                    "name": "lc-acbde",
                    "value": "de_DE",
                    "domain": ".amazon.de",
                    "path": "/",
                },
                {
                    "name": "i18n-prefs",
                    "value": "EUR",
                    "domain": ".amazon.de",
                    "path": "/",
                },
            ])
        elif 'polarnopyret.com' in domain:
            await context.add_cookies([
                {
                    "name": "pop.market",
                    "value": "EU",
                    "domain": ".polarnopyret.com",
                    "path": "/",
                },
                {
                    "name": "pop.country",
                    "value": "HUN",
                    "domain": ".polarnopyret.com",
                    "path": "/",
                },
                {
                    "name": "CookieConsent",
                    "value": "{stamp:%27u4fA4GyHyq9QOdrdYy8tE1qfHRpQjwYWCieQcbQGSNEz3dPdYu7BTn8DLDfLqOJKv8XB95eQ9fj56evg7MO/Fb4EzTBcTxL3cqr9ioJjQq1vhFYVv1Fz+fZXYHuqITNwVMdnJr1tBfwHj0R7AiWPBw==%27%2Cnecessary%3Atrue%2Cpreferences%3Atrue%2Cstatistics%3Atrue%2Cmarketing%3Atrue%2Cmethod%3A%27explicit%27%2Cver%3A1%2Cutc%3A1737431800000%2Cregion%3A%27hu%27}",
                    "domain": ".polarnopyret.com",
                    "path": "/",
                },
            ])

        page = await context.new_page()

        try:
            # Use domcontentloaded instead of networkidle (some sites never settle)
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)

            # Handle cookie consent dialogs (general)
            try:
                # Common cookie consent button selectors
                cookie_selectors = [
                    "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
                    "button[id*='accept']",
                    "button[class*='accept']",
                    "button:has-text('Accept')",
                    "button:has-text('Accept all')",
                    "button:has-text('Allow all')",
                    "#onetrust-accept-btn-handler",
                    ".cookie-accept",
                    "[data-testid='cookie-accept']",
                ]
                for selector in cookie_selectors:
                    try:
                        btn = page.locator(selector).first
                        if await btn.is_visible(timeout=1000):
                            await btn.click()
                            await page.wait_for_timeout(500)
                            break
                    except:
                        continue
            except:
                pass  # No cookie dialog or already dismissed

            # Wait for dynamic content to load
            await page.wait_for_timeout(5000)
            html = await page.content()
        finally:
            await browser.close()

    return html


def is_product_unavailable(soup: BeautifulSoup, html: str) -> bool:
    """Detect if a product page indicates the item is unavailable/out of stock."""
    unavailable_phrases = [
        "currently unavailable",
        "derzeit nicht verfügbar",
        "out of stock",
        "nicht auf lager",
        "sold out",
        "no longer available",
        "nicht mehr verfügbar",
        "produkt nicht verfügbar",
    ]

    # Check Amazon #availability element specifically
    avail_el = soup.select_one("#availability")
    if avail_el:
        avail_text = avail_el.get_text(strip=True).lower()
        for phrase in unavailable_phrases:
            if phrase in avail_text:
                return True

    # Check for common unavailability indicators in specific elements
    for selector in ["#outOfStock", "#soldOut", ".out-of-stock", ".sold-out", "[data-availability='outofstock']"]:
        if soup.select_one(selector):
            return True

    # Check JSON-LD availability
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            offers = data.get("offers", data.get("Offers", {}))
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            availability = str(offers.get("availability", "")).lower()
            if "outofstock" in availability or "discontinued" in availability:
                return True
        except (json.JSONDecodeError, AttributeError):
            pass

    return False


async def scrape_product_url(url: str) -> Dict[str, Any]:
    """
    Scrape a product URL and extract relevant information.
    Uses multiple extraction methods in priority order, with browser fallback.
    Returns a dict with: name, brand, price, currency, images, description, and other attributes.
    """
    result = {
        "url": url,
        "name": None,
        "brand": None,
        "model": None,
        "price": None,
        "currency": "EUR",
        "images": [],
        "description": None,
        "color": None,
        "size": None,
        "storage": None,
        "material": None,
        "retailer": None,
    }

    try:
        used_browser = False
        html = None
        soup = None

        # Extract retailer from domain
        domain = urlparse(url).netloc.replace("www.", "")
        result["retailer"] = domain.split(".")[0].capitalize()

        # Check if site requires browser rendering
        if needs_browser(url):
            html = await scrape_with_browser(url)
            used_browser = True
        else:
            # Try HTTP first
            try:
                async with httpx.AsyncClient(
                    follow_redirects=True,
                    timeout=15.0,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.5",
                    }
                ) as client:
                    response = await client.get(url)
                    response.raise_for_status()
                html = response.text
            except Exception:
                # HTTP failed (403, 400, timeout, etc.) - fall back to browser
                try:
                    html = await scrape_with_browser(url)
                    used_browser = True
                except Exception as browser_err:
                    raise Exception(f"Both HTTP and browser scraping failed: {browser_err}")

        soup = BeautifulSoup(html, "lxml")

        # --- Availability check ---
        # If the product is explicitly marked unavailable, return early with no price
        if is_product_unavailable(soup, html):
            result["error"] = "Product is currently unavailable"
            return clean_result(result, url)

        # --- Extraction pipeline (priority order) ---

        # Method 1: HTML patterns (shows actual displayed price)
        result = extract_html_patterns(soup, result)

        # Method 2: JSON-LD structured data
        result = extract_json_ld(soup, result)

        # Method 3: OpenGraph meta tags
        result = extract_opengraph(soup, result)

        # Method 4: Next.js __NEXT_DATA__ (React SSR sites)
        result = extract_next_data(soup, result)

        # Method 5: Inline JavaScript data (dataLayer, window vars, Nuxt, etc.)
        result = extract_inline_js_data(soup, result)

        # Method 6: Shopify product data (inline JSON and /products/*.json API)
        result = await extract_shopify_data(soup, result, url)

        # Method 7: Microdata (itemprop attributes)
        result = extract_microdata(soup, result)

        # Method 8: Standard meta tags
        result = extract_meta_tags(soup, result)

        # Method 9: Regex price extraction from raw HTML (last resort)
        if not result["price"]:
            result = extract_price_from_raw_html(html, result)

        # --- Browser fallback ---
        # If all methods failed to find a price and we haven't tried the browser yet, retry
        if not result["price"] and not used_browser:
            try:
                html = await scrape_with_browser(url)
                soup = BeautifulSoup(html, "lxml")

                # Re-run extraction methods on browser-rendered HTML
                result = extract_html_patterns(soup, result)
                if not result["price"]:
                    result = extract_json_ld(soup, result)
                if not result["price"]:
                    result = extract_next_data(soup, result)
                if not result["price"]:
                    result = extract_inline_js_data(soup, result)
                if not result["price"]:
                    result = extract_microdata(soup, result)
                if not result["price"]:
                    result = extract_price_from_raw_html(html, result)

                # Re-extract images from browser-rendered HTML if none found
                if not result["images"]:
                    result["images"] = extract_images(soup, url)
            except Exception:
                pass  # Browser fallback failed, continue with what we have

        # Extract all product images
        if not result["images"]:
            result["images"] = extract_images(soup, url)

        # Clean up the results
        result = clean_result(result, url)

    except Exception as e:
        result["error"] = str(e)

    return result


def extract_json_ld(soup: BeautifulSoup, result: Dict) -> Dict:
    """Extract product data from JSON-LD structured data."""
    scripts = soup.find_all("script", type="application/ld+json")

    for script in scripts:
        try:
            data = json.loads(script.string)

            # Handle array of items
            if isinstance(data, list):
                for item in data:
                    if item.get("@type") == "Product":
                        data = item
                        break
                else:
                    continue

            # Handle @graph structure
            if "@graph" in data:
                for item in data["@graph"]:
                    if item.get("@type") == "Product":
                        data = item
                        break
                else:
                    continue

            if data.get("@type") == "Product":
                if not result["name"] and data.get("name"):
                    result["name"] = data["name"]

                if not result["brand"]:
                    brand = data.get("brand")
                    if isinstance(brand, dict):
                        result["brand"] = brand.get("name")
                    elif isinstance(brand, str):
                        result["brand"] = brand

                if not result["description"] and data.get("description"):
                    result["description"] = data["description"][:500]

                # Extract price from offers
                offers = data.get("offers")
                if offers:
                    if isinstance(offers, list):
                        offers = offers[0]
                    if isinstance(offers, dict):
                        # Handle AggregateOffer type
                        if offers.get("@type") == "AggregateOffer":
                            if not result["price"]:
                                # Try lowPrice first, then highPrice
                                for price_field in ["lowPrice", "highPrice", "price"]:
                                    if offers.get(price_field):
                                        try:
                                            result["price"] = float(offers[price_field])
                                            break
                                        except:
                                            pass
                        elif not result["price"] and offers.get("price"):
                            try:
                                result["price"] = float(offers["price"])
                            except:
                                pass
                        if offers.get("priceCurrency"):
                            result["currency"] = offers["priceCurrency"]

                # Extract image
                image = data.get("image")
                if image:
                    if isinstance(image, str):
                        result["images"].append(image)
                    elif isinstance(image, list):
                        result["images"].extend([i if isinstance(i, str) else i.get("url", "") for i in image])
                    elif isinstance(image, dict):
                        result["images"].append(image.get("url", ""))

        except (json.JSONDecodeError, AttributeError):
            continue

    return result


def extract_opengraph(soup: BeautifulSoup, result: Dict) -> Dict:
    """Extract data from OpenGraph meta tags."""
    og_title = soup.find("meta", property="og:title")
    if og_title and not result["name"]:
        result["name"] = og_title.get("content")

    og_image = soup.find("meta", property="og:image")
    if og_image:
        img_url = og_image.get("content")
        if img_url and img_url not in result["images"]:
            result["images"].insert(0, img_url)

    og_description = soup.find("meta", property="og:description")
    if og_description and not result["description"]:
        result["description"] = og_description.get("content")

    # Product specific OG tags
    og_price = soup.find("meta", property="product:price:amount")
    if og_price and not result["price"]:
        try:
            result["price"] = float(og_price.get("content"))
        except:
            pass

    og_currency = soup.find("meta", property="product:price:currency")
    if og_currency:
        result["currency"] = og_currency.get("content")

    og_brand = soup.find("meta", property="product:brand")
    if og_brand and not result["brand"]:
        result["brand"] = og_brand.get("content")

    return result


def extract_meta_tags(soup: BeautifulSoup, result: Dict) -> Dict:
    """Extract data from standard meta tags."""
    # Title
    if not result["name"]:
        title = soup.find("title")
        if title:
            result["name"] = title.get_text().strip()

    # Description
    if not result["description"]:
        meta_desc = soup.find("meta", {"name": "description"})
        if meta_desc:
            result["description"] = meta_desc.get("content")

    return result


def extract_next_data(soup: BeautifulSoup, result: Dict) -> Dict:
    """Extract product data from Next.js __NEXT_DATA__ script tag (React SSR sites)."""
    next_script = soup.find("script", id="__NEXT_DATA__")
    if not next_script or not next_script.string:
        return result

    try:
        data = json.loads(next_script.string)
        page_props = data.get("props", {}).get("pageProps", {})

        # Look for product data in common locations
        product = (
            page_props.get("product") or
            page_props.get("productData") or
            page_props.get("item") or
            (page_props.get("data", {}).get("product") if isinstance(page_props.get("data"), dict) else None)
        )

        if isinstance(product, dict):
            # Extract name
            if not result["name"]:
                result["name"] = (
                    product.get("name") or
                    product.get("title") or
                    product.get("productName")
                )

            # Extract brand
            if not result["brand"]:
                brand = product.get("brand") or product.get("brandName")
                if isinstance(brand, dict):
                    result["brand"] = brand.get("name")
                elif isinstance(brand, str):
                    result["brand"] = brand

            # Extract description
            if not result["description"]:
                desc = product.get("description") or product.get("shortDescription")
                if desc:
                    result["description"] = desc[:500]

            # Extract price from nested prices dict (e.g., Axel Arigato: {EUR: {price: 260}})
            if not result["price"]:
                prices = product.get("prices")
                if isinstance(prices, dict):
                    # Try EUR first, then GBP, then USD, then first available
                    for currency_key in ["EUR", "GBP", "USD"]:
                        price_obj = prices.get(currency_key)
                        if isinstance(price_obj, dict):
                            sale = price_obj.get("sale_price") or price_obj.get("salePrice")
                            regular = price_obj.get("price") or price_obj.get("amount")
                            price_val = sale or regular
                            if price_val is not None:
                                try:
                                    result["price"] = float(price_val)
                                    result["currency"] = currency_key
                                    break
                                except (ValueError, TypeError):
                                    pass
                    # Fallback: take any currency
                    if not result["price"]:
                        for currency_key, price_obj in prices.items():
                            if isinstance(price_obj, dict):
                                price_val = price_obj.get("sale_price") or price_obj.get("price")
                                if price_val is not None:
                                    try:
                                        result["price"] = float(price_val)
                                        result["currency"] = price_obj.get("currency", currency_key)
                                        break
                                    except (ValueError, TypeError):
                                        pass

                # Try flat price fields
                if not result["price"]:
                    for field in ["price", "salePrice", "currentPrice", "displayPrice", "finalPrice"]:
                        val = product.get(field)
                        if val is not None:
                            try:
                                result["price"] = float(val)
                                break
                            except (ValueError, TypeError):
                                if isinstance(val, str):
                                    result["price"] = extract_price(val)
                                    if result["price"]:
                                        break

                # Try offers/variants
                if not result["price"]:
                    offers = product.get("offers") or product.get("variants", [])
                    if isinstance(offers, list) and offers:
                        first = offers[0]
                        if isinstance(first, dict):
                            price_val = first.get("price") or first.get("salePrice")
                            if price_val is not None:
                                try:
                                    result["price"] = float(price_val)
                                except (ValueError, TypeError):
                                    pass

            # Extract images
            images = product.get("images") or product.get("media") or product.get("gallery")
            if isinstance(images, dict):
                # Handle dict format like {main: "url", gallery: [...]}
                main_img = images.get("main") or images.get("src") or images.get("url")
                if main_img and main_img not in result["images"]:
                    result["images"].append(main_img)
                gallery = images.get("gallery") or images.get("items") or []
                if isinstance(gallery, list):
                    for img in gallery[:10]:
                        img_url = img if isinstance(img, str) else (img.get("url") or img.get("src") if isinstance(img, dict) else None)
                        if img_url and img_url not in result["images"]:
                            result["images"].append(img_url)
            elif isinstance(images, list):
                for img in images[:10]:
                    if isinstance(img, str):
                        if img not in result["images"]:
                            result["images"].append(img)
                    elif isinstance(img, dict):
                        img_url = img.get("url") or img.get("src") or img.get("image")
                        if img_url and img_url not in result["images"]:
                            result["images"].append(img_url)

        # Also search for product name in initialStory (Storyblok CMS)
        if not result["name"]:
            story = page_props.get("initialStory") or page_props.get("story")
            if isinstance(story, dict):
                result["name"] = story.get("name")

    except (json.JSONDecodeError, AttributeError, TypeError):
        pass

    return result


def extract_inline_js_data(soup: BeautifulSoup, result: Dict) -> Dict:
    """Extract product data from inline JavaScript variables and dataLayer pushes."""
    for script in soup.find_all("script"):
        text = script.string or ""
        if len(text) < 50:
            continue

        # --- Google Tag Manager dataLayer ---
        if "dataLayer" in text and not result["price"]:
            # Match dataLayer.push({...}) with product/ecommerce data
            dl_matches = re.findall(
                r'dataLayer\.push\((\{.*?\})\s*\)',
                text, re.DOTALL
            )
            for dl_str in dl_matches:
                try:
                    # JS object notation -> JSON (handle unquoted keys)
                    # Simple fix: try parsing as-is first
                    dl_data = json.loads(dl_str)
                    _extract_datalayer_price(dl_data, result)
                except json.JSONDecodeError:
                    # Try extracting price with regex from the raw string
                    _extract_price_from_js_fragment(dl_str, result)

        # --- Shopify product JSON ---
        if not result["price"] and "var meta" in text:
            # Shopify stores often embed: var meta = {"product": {...}}
            meta_match = re.search(r'var\s+meta\s*=\s*(\{.*?\})\s*;', text, re.DOTALL)
            if meta_match:
                try:
                    meta = json.loads(meta_match.group(1))
                    product = meta.get("product", {})
                    if not result["name"] and product.get("title"):
                        result["name"] = product["title"]
                    variants = product.get("variants", [])
                    if variants and isinstance(variants, list):
                        first = variants[0]
                        if isinstance(first, dict) and first.get("price"):
                            try:
                                # Shopify prices are in cents
                                price = float(first["price"]) / 100
                                if price > 0:
                                    result["price"] = price
                            except (ValueError, TypeError):
                                pass
                except (json.JSONDecodeError, TypeError):
                    pass

        # --- Generic window.* product data ---
        if not result["price"]:
            for var_pattern in [
                r'window\.__PRODUCT__\s*=\s*(\{.*?\})\s*;',
                r'window\.product\s*=\s*(\{.*?\})\s*;',
                r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;',
                r'window\.__PRELOADED_STATE__\s*=\s*(\{.*?\})\s*;',
                r'var\s+product\s*=\s*(\{.*?\})\s*;',
            ]:
                match = re.search(var_pattern, text, re.DOTALL)
                if match:
                    try:
                        obj = json.loads(match.group(1))
                        _extract_product_from_dict(obj, result)
                    except (json.JSONDecodeError, TypeError):
                        pass
                    if result["price"]:
                        break

        # --- Nuxt.js __NUXT__ ---
        if not result["price"] and "__NUXT__" in text:
            # Try to find price-like patterns in the Nuxt data
            _extract_price_from_js_fragment(text, result)

    return result


def _extract_datalayer_price(data: dict, result: Dict) -> None:
    """Extract price from a Google Tag Manager dataLayer push object."""
    # Standard ecommerce format
    ecommerce = data.get("ecommerce", {})
    if isinstance(ecommerce, dict):
        # Enhanced ecommerce
        for action_key in ["detail", "click", "add", "purchase", "impressions"]:
            action = ecommerce.get(action_key)
            if isinstance(action, dict):
                products = action.get("products") or action.get("actionField", {}).get("products", [])
            elif isinstance(action, list):
                products = action
            else:
                continue
            if isinstance(products, list):
                for p in products[:1]:
                    if isinstance(p, dict) and p.get("price"):
                        try:
                            if not result["price"]:
                                result["price"] = float(p["price"])
                            if not result["name"] and p.get("name"):
                                result["name"] = p["name"]
                            if not result["brand"] and p.get("brand"):
                                result["brand"] = p["brand"]
                        except (ValueError, TypeError):
                            pass

        # GA4 ecommerce format
        items = ecommerce.get("items") or ecommerce.get("products", [])
        if isinstance(items, list):
            for item in items[:1]:
                if isinstance(item, dict):
                    if not result["price"] and item.get("price"):
                        try:
                            result["price"] = float(item["price"])
                        except (ValueError, TypeError):
                            pass
                    if not result["name"] and item.get("item_name"):
                        result["name"] = item["item_name"]
                    if not result["brand"] and item.get("item_brand"):
                        result["brand"] = item["item_brand"]


def _extract_product_from_dict(obj: dict, result: Dict, depth: int = 0) -> None:
    """Recursively extract product data from a nested dict."""
    if depth > 4:
        return

    # Direct price fields
    if not result["price"]:
        for key in ["price", "salePrice", "sale_price", "currentPrice", "finalPrice", "displayPrice"]:
            val = obj.get(key)
            if val is not None:
                try:
                    price = float(val)
                    if price > 0:
                        result["price"] = price
                        break
                except (ValueError, TypeError):
                    if isinstance(val, str):
                        result["price"] = extract_price(val)
                        if result["price"]:
                            break

    if not result["name"]:
        result["name"] = obj.get("name") or obj.get("title") or obj.get("productName")

    if not result["brand"]:
        brand = obj.get("brand") or obj.get("brandName")
        if isinstance(brand, dict):
            result["brand"] = brand.get("name")
        elif isinstance(brand, str):
            result["brand"] = brand

    # Recurse into product-related sub-objects
    if not result["price"]:
        for key in ["product", "productData", "item", "data"]:
            sub = obj.get(key)
            if isinstance(sub, dict):
                _extract_product_from_dict(sub, result, depth + 1)
                if result["price"]:
                    return


def _extract_price_from_js_fragment(text: str, result: Dict) -> None:
    """Extract price from a JavaScript code fragment using regex patterns."""
    if result["price"]:
        return

    # Look for price-like assignments in JS
    patterns = [
        r'"price"\s*:\s*"?(\d+[\.,]?\d*)"?',
        r"'price'\s*:\s*'?(\d+[\.,]?\d*)'?",
        r'"salePrice"\s*:\s*"?(\d+[\.,]?\d*)"?',
        r'"sale_price"\s*:\s*"?(\d+[\.,]?\d*)"?',
        r'"currentPrice"\s*:\s*"?(\d+[\.,]?\d*)"?',
        r'"finalPrice"\s*:\s*"?(\d+[\.,]?\d*)"?',
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            try:
                price = float(match.group(1).replace(",", "."))
                if price > 0:
                    result["price"] = price
                    return
            except ValueError:
                pass


async def extract_shopify_data(soup: BeautifulSoup, result: Dict, url: str) -> Dict:
    """Extract product data from Shopify stores (inline JSON and /products/*.json API)."""
    # Method A: Look for Shopify product JSON in script tags
    for script in soup.find_all("script"):
        text = script.string or ""
        if not result["price"] and "ShopifyAnalytics" in text:
            # Shopify analytics meta
            meta_match = re.search(r'meta\s*=\s*(\{.*?\})\s*;', text, re.DOTALL)
            if meta_match:
                try:
                    meta = json.loads(meta_match.group(1))
                    product = meta.get("product", {})
                    variants = product.get("variants", [])
                    if variants:
                        first = variants[0]
                        price = first.get("price")
                        if price:
                            try:
                                result["price"] = float(price) / 100  # Shopify cents
                            except (ValueError, TypeError):
                                pass
                except (json.JSONDecodeError, TypeError):
                    pass

        # Look for product JSON object (Shopify theme)
        if not result["price"] and '"product"' in text and '"variants"' in text:
            prod_match = re.search(
                r'(?:var\s+)?product\s*=\s*(\{".*?"variants".*?\})\s*;',
                text, re.DOTALL
            )
            if prod_match:
                try:
                    product = json.loads(prod_match.group(1))
                    if not result["name"] and product.get("title"):
                        result["name"] = product["title"]
                    variants = product.get("variants", [])
                    if variants and isinstance(variants[0], dict):
                        price = variants[0].get("price")
                        if price:
                            try:
                                result["price"] = float(price) / 100
                            except (ValueError, TypeError):
                                pass
                except (json.JSONDecodeError, TypeError):
                    pass

    # Method B: Try Shopify /products/<handle>.json API endpoint
    if not result["price"]:
        parsed = urlparse(url)
        path_parts = parsed.path.strip("/").split("/")
        if path_parts:
            # Try the last path segment as product handle
            handle = path_parts[-1]
            # Remove query params from handle
            handle = handle.split("?")[0]
            if handle and not handle.startswith("_"):
                api_url = f"{parsed.scheme}://{parsed.netloc}/products/{handle}.json"
                try:
                    async with httpx.AsyncClient(
                        follow_redirects=True,
                        timeout=8.0,
                        headers={
                            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                            "Accept": "application/json",
                        }
                    ) as client:
                        resp = await client.get(api_url)
                        if resp.status_code == 200:
                            data = resp.json()
                            product = data.get("product", {})
                            if not result["name"] and product.get("title"):
                                result["name"] = product["title"]
                            if not result["brand"] and product.get("vendor"):
                                result["brand"] = product["vendor"]
                            if not result["description"] and product.get("body_html"):
                                desc_soup = BeautifulSoup(product["body_html"], "lxml")
                                result["description"] = desc_soup.get_text()[:500]
                            variants = product.get("variants", [])
                            if variants and isinstance(variants[0], dict):
                                price_str = variants[0].get("price")
                                if price_str:
                                    try:
                                        result["price"] = float(price_str)
                                    except (ValueError, TypeError):
                                        pass
                            images = product.get("images", [])
                            for img in images[:10]:
                                src = img.get("src") if isinstance(img, dict) else img
                                if src and src not in result["images"]:
                                    result["images"].append(src)
                except Exception:
                    pass  # Not a Shopify store or API not available

    return result


def extract_microdata(soup: BeautifulSoup, result: Dict) -> Dict:
    """Extract product data from HTML microdata (itemprop/itemscope attributes)."""
    # Find the product scope
    product_scope = soup.find(itemscope=True, itemtype=re.compile(r'schema\.org/Product', re.I))
    if not product_scope:
        # Try without full URL match
        product_scope = soup.find(attrs={"itemtype": re.compile(r'Product', re.I)})

    if not product_scope:
        return result

    # Extract name
    if not result["name"]:
        name_elem = product_scope.find(itemprop="name")
        if name_elem:
            result["name"] = name_elem.get("content") or name_elem.get_text().strip()

    # Extract brand
    if not result["brand"]:
        brand_elem = product_scope.find(itemprop="brand")
        if brand_elem:
            # Brand might be nested in its own itemscope
            name_in_brand = brand_elem.find(itemprop="name")
            if name_in_brand:
                result["brand"] = name_in_brand.get("content") or name_in_brand.get_text().strip()
            else:
                result["brand"] = brand_elem.get("content") or brand_elem.get_text().strip()

    # Extract description
    if not result["description"]:
        desc_elem = product_scope.find(itemprop="description")
        if desc_elem:
            result["description"] = (desc_elem.get("content") or desc_elem.get_text().strip())[:500]

    # Extract price from offer
    if not result["price"]:
        offer = product_scope.find(
            itemscope=True,
            itemtype=re.compile(r'schema\.org/Offer', re.I)
        )
        if not offer:
            offer = product_scope  # Some sites put itemprop=price directly on product

        price_elem = offer.find(itemprop="price") if offer else None
        if price_elem:
            price_text = price_elem.get("content") or price_elem.get_text()
            price_val = extract_price(price_text)
            if price_val:
                result["price"] = price_val

        # Extract currency
        currency_elem = offer.find(itemprop="priceCurrency") if offer else None
        if currency_elem:
            currency = currency_elem.get("content") or currency_elem.get_text().strip()
            if currency and len(currency) == 3:
                result["currency"] = currency.upper()

    # Extract image
    if not result["images"]:
        img_elem = product_scope.find(itemprop="image")
        if img_elem:
            src = img_elem.get("content") or img_elem.get("src") or img_elem.get("href")
            if src:
                result["images"].append(src)

    return result


def extract_price_from_raw_html(html: str, result: Dict) -> Dict:
    """
    Last-resort: extract price from raw HTML using regex patterns.
    Looks for common price display patterns in the HTML source.
    """
    if result["price"]:
        return result

    # Pattern 1: Currency symbol followed by number (€260, $365, £240)
    currency_map = {"€": "EUR", "$": "USD", "£": "GBP", "¥": "JPY"}
    for symbol, currency in currency_map.items():
        matches = re.findall(
            rf'{re.escape(symbol)}\s*(\d{{1,6}}(?:[.,]\d{{2}})?)(?!\d)',
            html
        )
        if matches:
            try:
                price = float(matches[0].replace(",", "."))
                if price > 0:
                    result["price"] = price
                    result["currency"] = currency
                    return result
            except ValueError:
                pass

    # Pattern 2: Number followed by currency symbol (260€, 365$)
    for symbol, currency in currency_map.items():
        matches = re.findall(
            rf'(\d{{1,6}}(?:[.,]\d{{2}})?)\s*{re.escape(symbol)}',
            html
        )
        if matches:
            try:
                price = float(matches[0].replace(",", "."))
                if price > 0:
                    result["price"] = price
                    result["currency"] = currency
                    return result
            except ValueError:
                pass

    return result


def extract_html_patterns(soup: BeautifulSoup, result: Dict) -> Dict:
    """Extract data from common HTML patterns."""
    # Common product title selectors
    title_selectors = [
        "h1.product-title",
        "h1.product-name",
        "h1[itemprop='name']",
        ".product-title h1",
        ".product-name",
        "#productTitle",
        "[data-testid='product-title']",
        "h1",
    ]

    if not result["name"]:
        for selector in title_selectors:
            elem = soup.select_one(selector)
            if elem:
                text = elem.get_text().strip()
                if text and len(text) < 300:
                    result["name"] = text
                    break

    # Common price selectors (sale/final price first, then regular)
    # IMPORTANT: Main product price selectors with IDs should come FIRST
    # to avoid picking up prices from related/recommended product blocks
    price_selectors = [
        # Prestashop main product price (e.g. agynemustore.hu)
        "#our_price_display",  # Prestashop main product price
        "#product_price",  # Alternative Prestashop price ID
        # ShopRenter main product price (e.g. edesalom.hu)
        ".product_table_price .price",  # ShopRenter main price
        "span.price.price_color.product_table_price",  # ShopRenter specific
        # Amazon selectors (modern)
        ".a-price .a-offscreen",  # Amazon current price (hidden for accessibility)
        "#corePrice_feature_div .a-offscreen",  # Amazon core price
        "#corePriceDisplay_desktop_feature_div .a-offscreen",  # Amazon desktop price
        ".priceToPay .a-offscreen",  # Amazon price to pay
        "#priceblock_ourprice",  # Amazon legacy
        "#priceblock_dealprice",  # Amazon deal price
        "#priceblock_saleprice",  # Amazon sale price
        # Sportano
        ".c-price__price--discount",  # Sportano sale/discount price (not the --old one)
        ".c-price__price--normal:not(.c-price__price--old)",  # Sportano normal price (exclude old)
        ".c-price--type-productpage .c-price__local",  # Sportano main product price
        ".c-price__local",  # Sportano local price
        ".final-price--discount",  # Sportano discount price
        # Patagonia
        ".sales.text-sales-price .value",  # Patagonia sale price
        ".sales .value",  # Generic sale price
        ".text-sales-price",  # Patagonia sale
        # Other sites
        ".inActionPrice",  # Mountex sale price
        ".price-final_price .price",  # Magento final price
        ".normal-price .price",  # Magento normal price
        ".special-price .price",  # Magento special/sale price
        "[data-price-type='finalPrice']",  # Magento data attribute
        "[itemprop='price']",
        ".js-price",  # Bergfreunde main price
        ".product-detail-price",
        ".product__price",
        "[data-testid='product-price']",
        "[data-testid='price']",
        "[data-product-price]",
        "[data-price]",
        ".f-price-item--sale",  # Shopify/Pipeline
        ".price__amount",
        ".price-current",
        ".product-price",
        ".current-price",
        ".sale-price",
        ".price--sale",
        ".price-item--sale",
        ".price-tag__amount",
        ".f-price-item--regular",  # Shopify/Pipeline fallback
        ".price",
    ]

    if not result["price"]:
        for selector in price_selectors:
            elem = soup.select_one(selector)
            if elem:
                # Try content attribute first, then data attributes, then text
                price_text = (
                    elem.get("content") or
                    elem.get("data-product-price") or
                    elem.get("data-price") or
                    elem.get_text()
                )
                price = extract_price(price_text)
                if price:
                    result["price"] = price
                    # Check for HUF currency in the text
                    full_text = elem.get_text()
                    if "Ft" in full_text or "HUF" in full_text:
                        result["currency"] = "HUF"
                    break

    # Try to extract brand from common patterns
    if not result["brand"]:
        brand_selectors = [
            "[itemprop='brand']",
            ".product-brand",
            ".brand-name",
            "[data-testid='brand']",
        ]
        for selector in brand_selectors:
            elem = soup.select_one(selector)
            if elem:
                brand = elem.get("content") or elem.get_text().strip()
                if brand:
                    result["brand"] = brand
                    break

    return result


def extract_images(soup: BeautifulSoup, base_url: str) -> List[str]:
    """Extract product images from the page."""
    images = []
    seen = set()

    # Common image selectors for product images
    image_selectors = [
        "[itemprop='image']",
        ".product-image img",
        ".product-gallery img",
        "#product-image img",
        "[data-testid='product-image']",
        ".gallery img",
        ".product-photo img",
        ".main-image img",
    ]

    for selector in image_selectors:
        for img in soup.select(selector):
            src = img.get("src") or img.get("data-src") or img.get("data-lazy-src")
            if src and src not in seen:
                # Make absolute URL
                if src.startswith("//"):
                    src = "https:" + src
                elif src.startswith("/"):
                    parsed = urlparse(base_url)
                    src = f"{parsed.scheme}://{parsed.netloc}{src}"

                # Filter out tiny images and icons
                if is_valid_product_image(src):
                    images.append(src)
                    seen.add(src)

            if len(images) >= 10:
                break
        if len(images) >= 10:
            break

    # If no images found with specific selectors, try general img tags
    if not images:
        for img in soup.find_all("img"):
            src = img.get("src") or img.get("data-src")
            if src and src not in seen:
                if src.startswith("//"):
                    src = "https:" + src
                elif src.startswith("/"):
                    parsed = urlparse(base_url)
                    src = f"{parsed.scheme}://{parsed.netloc}{src}"

                if is_valid_product_image(src):
                    images.append(src)
                    seen.add(src)

            if len(images) >= 10:
                break

    return images


def is_valid_product_image(url: str) -> bool:
    """Check if URL looks like a valid product image."""
    url_lower = url.lower()

    # Skip tiny images, icons, logos
    skip_patterns = [
        "icon", "logo", "sprite", "pixel", "tracking",
        "1x1", "blank", "spacer", "transparent",
        ".gif", ".svg", "base64",
    ]

    for pattern in skip_patterns:
        if pattern in url_lower:
            return False

    # Must have image extension or look like image URL
    image_indicators = [".jpg", ".jpeg", ".png", ".webp", "image", "photo", "product"]
    return any(ind in url_lower for ind in image_indicators)


def extract_price(text: str) -> Optional[float]:
    """Extract a numeric price from text."""
    if not text:
        return None

    # Strip whitespace first
    text = text.strip()

    # Remove currency symbols
    text = re.sub(r"[€$£¥₹]", "", text)

    # Remove currency text suffixes (Ft, HUF, EUR, etc.)
    text = re.sub(r"\s*(Ft|HUF|EUR|USD|GBP)\s*$", "", text, flags=re.IGNORECASE)

    # Remove all whitespace including non-breaking spaces (\xa0)
    text = re.sub(r"[\s\xa0]+", "", text)

    # Handle European thousands separator: period followed by exactly 3 digits
    # e.g., "95.800" -> "95800" (Hungarian format)
    # But keep "95.80" as is (2 digits after = decimal)
    text = re.sub(r"\.(\d{3})(?!\d)", r"\1", text)

    # Handle European format: comma as decimal separator
    # Pattern: digits,2digits at end -> decimal
    text = re.sub(r",(\d{2})$", r".\1", text)

    # Remove any remaining commas (thousands separators)
    text = text.replace(",", "")

    # Find the first number (including decimals)
    match = re.search(r"(\d+\.?\d*)", text)
    if match:
        try:
            return float(match.group(1))
        except:
            pass

    return None


def clean_result(result: Dict, url: str = "") -> Dict:
    """Clean up the result dict."""
    # Clean name
    if result["name"]:
        # Remove common suffixes
        name = result["name"]
        for suffix in [" - Amazon", " | eBay", " - Best Buy", " - Walmart"]:
            name = name.replace(suffix, "")
        result["name"] = name.strip()[:200]

    # Clean description
    if result["description"]:
        result["description"] = result["description"][:500]

    # Deduplicate images and filter out relative/invalid URLs
    if result["images"]:
        seen = set()
        unique = []
        for img in result["images"]:
            if img and img.startswith("http") and img not in seen:
                unique.append(img)
                seen.add(img)
        result["images"] = unique[:10]

    # Fix currency for Hungarian sites - they use HUF, not EUR
    # If the domain is .hu and currency is still EUR (the default), change to HUF
    if url:
        domain = urlparse(url).netloc.lower()
        if domain.endswith('.hu') and result.get("currency") == "EUR":
            # Check if the price makes sense for HUF (typically > 1000)
            if result.get("price") and result["price"] > 1000:
                result["currency"] = "HUF"

    return result
