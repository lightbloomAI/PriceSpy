from fastapi import APIRouter, HTTPException
from typing import List
from .. import database
from ..services.scraper import scrape_product_prices
from ..services.alerts import check_and_send_alert
from ..services.currency import convert_price

router = APIRouter(prefix="/api/prices", tags=["prices"])


@router.get("/{product_id}/history")
async def get_price_history(product_id: int, limit: int = 50):
    """Get price history for a product."""
    product = await database.get_product(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    history = await database.get_price_history(product_id, limit=limit)
    return history


@router.get("/{product_id}/latest")
async def get_latest_prices(product_id: int):
    """Get the latest price from each retailer for a product."""
    product = await database.get_product(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    prices = await database.get_latest_prices(product_id)
    return prices


@router.post("/{product_id}/scrape")
async def scrape_product(product_id: int):
    """Manually trigger a price scrape for a product."""
    product = await database.get_product(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    # Get excluded sources
    excluded_sources = await database.get_excluded_sources(product_id)

    # Scrape prices with all product attributes
    prices = await scrape_product_prices(
        product_id=product_id,
        search_query=product["search_query"],
        region=product.get("region", "eu"),
        size=product.get("size"),
        color=product.get("color"),
        brand=product.get("brand"),
        model=product.get("model"),
        storage=product.get("storage"),
        material=product.get("material"),
    )

    if not prices:
        return {"message": "No prices found", "prices": []}

    # Filter out excluded sources
    filtered_prices = [p for p in prices if p["retailer"] not in excluded_sources]
    excluded_count = len(prices) - len(filtered_prices)

    # Store prices in database with original currency (only non-excluded)
    for price_data in filtered_prices:
        await database.add_price_record(
            product_id=product_id,
            retailer=price_data["retailer"],
            price=price_data["price"],
            url=price_data["url"],
            currency=price_data.get("currency", "EUR"),
        )

    # Check for alerts (only from non-excluded sources)
    alert_sent = False
    if filtered_prices:
        lowest = min(filtered_prices, key=lambda x: x["price"])
        alert_sent = await check_and_send_alert(
            product=product,
            lowest_price=lowest["price"],
            retailer=lowest["retailer"],
            url=lowest["url"],
        )

    return {
        "message": f"Found {len(filtered_prices)} prices ({excluded_count} excluded)",
        "prices": filtered_prices,
        "alert_sent": alert_sent,
    }


@router.post("/scrape-all")
async def scrape_all_products():
    """Trigger a price scrape for all active products."""
    products = await database.get_all_products(active_only=True)

    results = []
    for product in products:
        try:
            # Get excluded sources for this product
            excluded_sources = await database.get_excluded_sources(product["id"])

            prices = await scrape_product_prices(
                product_id=product["id"],
                search_query=product["search_query"],
                region=product.get("region", "eu"),
                size=product.get("size"),
                color=product.get("color"),
                brand=product.get("brand"),
                model=product.get("model"),
                storage=product.get("storage"),
                material=product.get("material"),
            )

            # Filter out excluded sources
            filtered_prices = [p for p in prices if p["retailer"] not in excluded_sources]

            # Store prices with original currency
            for price_data in filtered_prices:
                await database.add_price_record(
                    product_id=product["id"],
                    retailer=price_data["retailer"],
                    price=price_data["price"],
                    url=price_data["url"],
                    currency=price_data.get("currency", "EUR"),
                )

            # Check for alerts (only from non-excluded sources)
            if filtered_prices:
                lowest = min(filtered_prices, key=lambda x: x["price"])
                alert_sent = await check_and_send_alert(
                    product=product,
                    lowest_price=lowest["price"],
                    retailer=lowest["retailer"],
                    url=lowest["url"],
                )
            else:
                alert_sent = False

            results.append({
                "product_id": product["id"],
                "product_name": product["name"],
                "prices_found": len(filtered_prices),
                "excluded_count": len(prices) - len(filtered_prices),
                "alert_sent": alert_sent,
            })

        except Exception as e:
            results.append({
                "product_id": product["id"],
                "product_name": product["name"],
                "error": str(e),
            })

    return {"results": results}
