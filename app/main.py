from fastapi import FastAPI, Request, Form
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse, JSONResponse
from starlette.middleware.sessions import SessionMiddleware
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional, List
from pydantic import BaseModel

from . import database
from .config import get_settings
from .auth import AuthMiddleware, verify_password
from .routers import products, prices
from .services.url_scraper import scrape_product_url
from .services.currency import convert_price
from .services.country import detect_country
from .services.scraper import scrape_product_prices
from .services.alerts import check_and_send_alert


class ScrapeRequest(BaseModel):
    url: str


class ReorderRequest(BaseModel):
    product_ids: List[int]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize database
    await database.init_db()
    yield
    # Shutdown: Close database connection pool
    await database.close_db()


app = FastAPI(
    title="PriceSpy",
    description="Price monitoring and alert system",
    version="1.0.0",
    lifespan=lifespan,
)

# Middleware (order matters: auth runs first, then session decodes cookie)
settings = get_settings()
app.add_middleware(AuthMiddleware)
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key)

# Mount static files
static_path = Path(__file__).parent / "static"
static_path.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=static_path), name="static")

# Templates
templates_path = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=templates_path)

# Include API routers
app.include_router(products.router)
app.include_router(prices.router)


# Web UI Routes
@app.get("/")
async def home(request: Request):
    """Home page - list all products."""
    products_list = await database.get_all_products()

    # Enrich with latest prices and sources (only included sources)
    for product in products_list:
        latest_prices = await database.get_latest_prices(product["id"])
        excluded_sources = await database.get_excluded_sources(product["id"])
        # Filter to only included sources
        included_prices = [p for p in latest_prices if p["retailer"] not in excluded_sources]
        if included_prices:
            product["lowest_price"] = included_prices[0]["price"]
            product["lowest_price_currency"] = included_prices[0].get("currency", "EUR")
            product["lowest_price_retailer"] = included_prices[0]["retailer"]
            product["lowest_price_url"] = included_prices[0]["url"]
            product["sources"] = included_prices  # Only included sources
            # Convert lowest price to target currency for deal comparison
            source_currency = included_prices[0].get("currency", "EUR")
            target_currency = product.get("currency", "EUR")
            if source_currency != target_currency:
                converted = await convert_price(included_prices[0]["price"], source_currency, target_currency)
                product["lowest_price_converted"] = converted
            else:
                product["lowest_price_converted"] = included_prices[0]["price"]
        else:
            product["lowest_price"] = None
            product["lowest_price_currency"] = None
            product["lowest_price_retailer"] = None
            product["lowest_price_url"] = None
            product["lowest_price_converted"] = None
            product["sources"] = []

    return templates.TemplateResponse(
        "index.html",
        {"request": request, "products": products_list, "user_email": request.session.get("user_email")}
    )


@app.get("/add")
async def add_product_form(request: Request, category: Optional[str] = None):
    """Show add product form."""
    return templates.TemplateResponse(
        "add_product.html",
        {"request": request, "category": category or "electronics", "user_email": request.session.get("user_email")}
    )


@app.post("/add")
async def add_product_submit(
    request: Request,
    name: str = Form(...),
    search_query: str = Form(...),
    category: str = Form(...),
    region: str = Form(...),
    target_price: float = Form(...),
    currency: str = Form("EUR"),
    user_email: str = Form(...),
    size: Optional[str] = Form(None),
    color: Optional[str] = Form(None),
    brand: Optional[str] = Form(None),
    model: Optional[str] = Form(None),
    storage: Optional[str] = Form(None),
    material: Optional[str] = Form(None),
    image_url: Optional[str] = Form(None),
    source_url: Optional[str] = Form(None),
    source_price: Optional[float] = Form(None),
    source_currency: Optional[str] = Form(None),
):
    """Handle add product form submission."""
    product_id = await database.create_product(
        name=name,
        search_query=search_query,
        category=category,
        region=region,
        target_price=target_price,
        currency=currency,
        user_email=user_email,
        size=size if size else None,
        color=color if color else None,
        brand=brand if brand else None,
        model=model if model else None,
        storage=storage if storage else None,
        material=material if material else None,
        image_url=image_url if image_url else None,
    )

    # If a source URL was fetched, add it as a source immediately
    if source_url and source_price and product_id:
        from urllib.parse import urlparse as _urlparse
        retailer = _urlparse(source_url).netloc.replace("www.", "")
        await database.add_price_record(
            product_id=product_id,
            retailer=retailer,
            price=source_price,
            url=source_url,
            currency=source_currency or currency,
        )
        await database.update_source_status(product_id, retailer, success=True)

    return RedirectResponse(url="/", status_code=303)


@app.get("/product/{product_id}")
async def product_detail(request: Request, product_id: int):
    """Product detail page with price history."""
    product = await database.get_product(product_id)
    if not product:
        return RedirectResponse(url="/", status_code=303)

    price_history = await database.get_price_history(product_id, limit=100)
    latest_prices = await database.get_latest_prices(product_id)
    excluded_sources = await database.get_excluded_sources(product_id)

    # Get unique sources from price history with their details
    # price_history is sorted by scraped_at DESC, so first occurrence has latest data
    sources = {}
    for record in price_history:
        retailer = record['retailer']
        if retailer not in sources:
            # Detect country from retailer name and URL
            country_code, country_name = detect_country(retailer, record.get('url', ''))
            sources[retailer] = {
                'retailer': retailer,
                'currency': record.get('currency', 'EUR'),
                'url': record.get('url', ''),
                'latest_price': record['price'],
                'is_excluded': retailer in excluded_sources,
                'country_code': country_code,
                'country_name': country_name,
            }
        # If current record has a URL but stored one doesn't, update it
        elif not sources[retailer]['url'] and record.get('url'):
            sources[retailer]['url'] = record.get('url')

    # Convert all source prices to HUF for sorting and display
    for source in sources.values():
        source_currency = source.get('currency', 'EUR')
        if source_currency == 'HUF':
            source['price_huf'] = source['latest_price']
        else:
            converted = await convert_price(source['latest_price'], source_currency, 'HUF')
            source['price_huf'] = converted if converted else None

    # Sort sources by: excluded status (included first), then HUF price (cheapest first), None values at the end
    sources_list = sorted(
        sources.values(),
        key=lambda x: (x['is_excluded'], x['price_huf'] is None, x['price_huf'] or float('inf'))
    )

    # Get source statuses for scraping success/failure indicators
    source_statuses = await database.get_source_statuses(product_id)
    for source in sources_list:
        status = source_statuses.get(source['retailer'])
        if status:
            source['scrape_success'] = status['success']
            source['scrape_error'] = status.get('error_message')
            source['last_checked'] = status.get('last_checked_at')
        else:
            # No status recorded yet - assume success since we have price data
            source['scrape_success'] = True
            source['scrape_error'] = None
            source['last_checked'] = None

    # Get lowest price from included sources only
    lowest_converted = None
    included_prices = [p for p in latest_prices if p['retailer'] not in excluded_sources]
    if included_prices:
        lowest = included_prices[0]
        source_currency = lowest.get('currency', 'EUR')
        target_currency = product.get('currency', 'EUR')
        if source_currency != target_currency:
            converted = await convert_price(lowest['price'], source_currency, target_currency)
            if converted is not None:
                lowest_converted = {
                    'price': converted,
                    'currency': target_currency,
                    'retailer': lowest['retailer']
                }
        else:
            # Same currency, no conversion needed
            lowest_converted = {
                'price': lowest['price'],
                'currency': source_currency,
                'retailer': lowest['retailer']
            }

    return templates.TemplateResponse(
        "product.html",
        {
            "request": request,
            "product": product,
            "product_image": product.get("image_url"),
            "price_history": price_history,
            "latest_prices": included_prices,  # Only included sources for stat cards
            "sources": sources_list,
            "excluded_sources": excluded_sources,
            "lowest_converted": lowest_converted,
            "user_email": request.session.get("user_email"),
        }
    )


@app.post("/product/{product_id}/delete")
async def delete_product_web(product_id: int):
    """Delete a product from web UI."""
    await database.delete_product(product_id)
    return RedirectResponse(url="/", status_code=303)


@app.get("/product/{product_id}/edit")
async def edit_product_form(request: Request, product_id: int):
    """Show edit product form."""
    product = await database.get_product(product_id)
    if not product:
        return RedirectResponse(url="/", status_code=303)

    return templates.TemplateResponse(
        "edit_product.html",
        {"request": request, "product": product, "user_email": request.session.get("user_email")}
    )


@app.post("/product/{product_id}/edit")
async def edit_product_submit(
    product_id: int,
    name: str = Form(...),
    search_query: str = Form(...),
    category: str = Form(...),
    region: str = Form(...),
    target_price: float = Form(...),
    currency: str = Form("EUR"),
    user_email: str = Form(...),
    size: Optional[str] = Form(None),
    color: Optional[str] = Form(None),
    brand: Optional[str] = Form(None),
    model: Optional[str] = Form(None),
    storage: Optional[str] = Form(None),
    material: Optional[str] = Form(None),
):
    """Handle edit product form submission."""
    await database.update_product(
        product_id,
        name=name,
        search_query=search_query,
        category=category,
        region=region,
        target_price=target_price,
        currency=currency,
        user_email=user_email,
        size=size if size else None,
        color=color if color else None,
        brand=brand if brand else None,
        model=model if model else None,
        storage=storage if storage else None,
        material=material if material else None,
    )
    return RedirectResponse(url=f"/product/{product_id}", status_code=303)


@app.post("/product/{product_id}/toggle")
async def toggle_product_web(product_id: int):
    """Toggle product active status from web UI."""
    product = await database.get_product(product_id)
    if product:
        await database.update_product(product_id, is_active=not product["is_active"])
    return RedirectResponse(url="/", status_code=303)


@app.post("/api/products/reorder")
async def reorder_products_endpoint(request: ReorderRequest):
    """Reorder products by setting sort_order based on provided ID list."""
    await database.reorder_products(request.product_ids)
    return JSONResponse(content={"success": True})


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}


# Auth routes
@app.get("/login")
async def login_page(request: Request):
    # Already logged in? Redirect home.
    if request.session.get("user_email"):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
async def login_submit(request: Request, email: str = Form(...), password: str = Form(...)):
    user = await database.get_user_by_email(email)
    if user and verify_password(password, user["password_hash"]):
        request.session["user_email"] = user["email"]
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": "Invalid email or password"},
        status_code=401,
    )


@app.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


@app.post("/api/scrape-url")
async def scrape_url_endpoint(request: ScrapeRequest):
    """Scrape a product URL and return extracted data."""
    try:
        data = await scrape_product_url(request.url)
        return JSONResponse(content=data)
    except Exception as e:
        return JSONResponse(
            content={"error": str(e)},
            status_code=400
        )


class AddSourceRequest(BaseModel):
    url: str


@app.post("/api/product/{product_id}/add-source")
async def add_source_endpoint(product_id: int, request: AddSourceRequest):
    """Add a new source from URL."""
    from urllib.parse import urlparse
    parsed = urlparse(request.url)
    retailer = parsed.netloc.replace("www.", "")

    try:
        # Get product to check it exists
        product = await database.get_product(product_id)
        if not product:
            return JSONResponse(content={"error": "Product not found"}, status_code=404)

        # Scrape the URL
        data = await scrape_product_url(request.url)

        if data.get("error"):
            # Record failed scrape
            await database.update_source_status(product_id, retailer, success=False, error_message=data.get("error"))
            return JSONResponse(content={"error": data.get("error")}, status_code=400)

        if not data.get("price"):
            # Record failed scrape
            await database.update_source_status(product_id, retailer, success=False, error_message="Could not extract price")
            return JSONResponse(content={"error": "Could not extract price from URL"}, status_code=400)

        # Use scraped price and currency, or defaults
        price = data.get("price", 0)
        currency = data.get("currency", "EUR")

        # Add to database
        await database.add_price_record(
            product_id=product_id,
            retailer=retailer,
            price=price,
            url=request.url,
            currency=currency,
        )

        # Record successful scrape
        await database.update_source_status(product_id, retailer, success=True)

        # Check for deal and send email alert
        await check_and_send_alert(product, price, retailer, request.url, currency)

        # Save product image if the product doesn't have one yet
        if not product.get("image_url"):
            images = data.get("images", [])
            good_images = [img for img in images if img.startswith("http")]
            if good_images:
                await database.update_product(product_id, image_url=good_images[0])

        return JSONResponse(content={
            "success": True,
            "retailer": retailer,
            "price": price,
            "currency": currency,
        })

    except Exception as e:
        # Record failed scrape
        await database.update_source_status(product_id, retailer, success=False, error_message=str(e))
        return JSONResponse(content={"error": str(e)}, status_code=400)


@app.post("/product/{product_id}/exclude-source")
async def exclude_source_web(product_id: int, retailer: str = Form(...)):
    """Exclude a source from tracking for this product."""
    await database.exclude_source(product_id, retailer)
    return RedirectResponse(url=f"/product/{product_id}", status_code=303)


@app.post("/product/{product_id}/include-source")
async def include_source_web(product_id: int, retailer: str = Form(...)):
    """Re-include a previously excluded source."""
    await database.include_source(product_id, retailer)
    return RedirectResponse(url=f"/product/{product_id}", status_code=303)


@app.post("/api/product/{product_id}/find-sources")
async def find_sources_endpoint(product_id: int):
    """Search for new sources from Google Shopping (keeps existing, respects excluded)."""
    try:
        product = await database.get_product(product_id)
        if not product:
            return JSONResponse(content={"error": "Product not found"}, status_code=404)

        # Get excluded sources and existing sources
        excluded_sources = await database.get_excluded_sources(product_id)
        existing_prices = await database.get_latest_prices(product_id)
        existing_retailers = {p["retailer"] for p in existing_prices}

        # Scrape prices from Google Shopping
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

        # Save only NEW sources (skip excluded and already existing)
        new_count = 0
        first_thumbnail = None
        for price_data in prices:
            retailer = price_data["retailer"]

            # Skip excluded sources
            if retailer in excluded_sources:
                continue

            # Grab thumbnail for product image
            if not first_thumbnail and price_data.get("thumbnail"):
                first_thumbnail = price_data["thumbnail"]

            # Add price record (updates if exists, adds if new)
            await database.add_price_record(
                product_id=product_id,
                retailer=retailer,
                price=price_data["price"],
                url=price_data.get("url", ""),
                currency=price_data.get("currency", "EUR"),
            )
            await database.update_source_status(product_id, retailer, success=True)

            # Check for deal and send email alert
            await check_and_send_alert(product, price_data["price"], retailer, price_data.get("url", ""), price_data.get("currency", "EUR"))

            if retailer not in existing_retailers:
                new_count += 1

        # Save product image if needed
        if first_thumbnail and not product.get("image_url"):
            await database.update_product(product_id, image_url=first_thumbnail)

        return JSONResponse(content={
            "success": True,
            "new_sources": new_count,
            "total_updated": len(prices) - len([r for r in prices if r["retailer"] in excluded_sources]),
        })

    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=400)


@app.post("/api/product/{product_id}/refresh-prices")
async def refresh_prices_endpoint(product_id: int):
    """Refresh prices for existing sources only (scrapes their URLs)."""
    try:
        product = await database.get_product(product_id)
        if not product:
            return JSONResponse(content={"error": "Product not found"}, status_code=404)

        # Get existing sources with their URLs
        existing_prices = await database.get_latest_prices(product_id)
        excluded_sources = await database.get_excluded_sources(product_id)

        updated_count = 0
        failed_count = 0

        for source in existing_prices:
            retailer = source["retailer"]
            url = source.get("url", "")

            # Skip excluded sources
            if retailer in excluded_sources:
                continue

            # Skip Google Shopping URLs - can't scrape them directly
            if not url or 'google.com' in url:
                continue

            try:
                # Scrape the retailer URL for current price
                data = await scrape_product_url(url)

                if data.get("price"):
                    await database.add_price_record(
                        product_id=product_id,
                        retailer=retailer,
                        price=data["price"],
                        url=url,
                        currency=data.get("currency", source.get("currency", "EUR")),
                    )
                    await database.update_source_status(product_id, retailer, success=True)

                    # Check for deal and send email alert
                    await check_and_send_alert(product, data["price"], retailer, url, data.get("currency", source.get("currency", "EUR")))

                    updated_count += 1
                else:
                    await database.update_source_status(
                        product_id, retailer, success=False,
                        error_message="Could not extract price"
                    )
                    failed_count += 1

            except Exception as e:
                await database.update_source_status(
                    product_id, retailer, success=False, error_message=str(e)
                )
                failed_count += 1

        return JSONResponse(content={
            "success": True,
            "updated": updated_count,
            "failed": failed_count,
            "skipped_google": len([s for s in existing_prices if 'google.com' in s.get("url", "")]),
        })

    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=400)
