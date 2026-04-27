"""
Diagnostic for Amazon price scraping.

Usage:
    python3 scripts/check_amazon.py "<amazon url>"

Prints what each candidate price selector returns, so we can tell whether
Amazon is serving a different variation/page from this server's IP.
"""
import asyncio
import sys
from bs4 import BeautifulSoup

sys.path.insert(0, ".")
from app.services.url_scraper import scrape_with_browser, scrape_product_url


async def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else (
        "https://www.amazon.de/-/en/DJI-Transmitters-Receiver-Microphone-Ultralight/dp/B0F995J8FR/?th=1"
    )

    html = await scrape_with_browser(url)
    soup = BeautifulSoup(html, "lxml")

    def show(label: str, sel: str, attr: str = "text") -> None:
        el = soup.select_one(sel)
        if not el:
            print(f"{label}: MISSING")
            return
        if attr == "text":
            print(f"{label}: {el.get_text().strip()[:160]!r}")
        else:
            print(f"{label}: {el.get(attr)!r}")

    print(f"URL: {url}")
    show("title", "#productTitle")
    show("twister-plus value", "#twister-plus-price-data-price", "value")
    show("apex-pricetopay-accessibility-label", "#apex-pricetopay-accessibility-label")
    show("corePriceDisplay_desktop .a-price-whole", "#corePriceDisplay_desktop_feature_div .a-price-whole")
    show("corePriceDisplay_desktop .a-price-fraction", "#corePriceDisplay_desktop_feature_div .a-price-fraction")
    show("corePrice_feature .a-offscreen", "#corePrice_feature_div .a-offscreen")
    show("priceblock_ourprice", "#priceblock_ourprice")

    print()
    print("--- full scrape result ---")
    result = await scrape_product_url(url)
    for key in ("price", "currency", "name", "error"):
        print(f"  {key}: {result.get(key)}")


if __name__ == "__main__":
    asyncio.run(main())
