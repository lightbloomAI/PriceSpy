import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional
from ..config import get_settings


CURRENCY_SYMBOLS = {
    "USD": "$",
    "EUR": "\u20ac",
    "GBP": "\u00a3",
    "HUF": "Ft",
    "SEK": "kr",
    "DKK": "kr",
    "NOK": "kr",
    "CHF": "CHF",
    "PLN": "z\u0142",
    "CZK": "K\u010d",
}


def format_price(price: float, currency: str = "EUR") -> str:
    """Format price with currency symbol."""
    symbol = CURRENCY_SYMBOLS.get(currency, currency)
    if currency == "HUF":
        return f"{price:,.0f} {symbol}"
    if currency in ("SEK", "DKK", "NOK", "CZK", "PLN"):
        return f"{price:,.2f} {symbol}"
    return f"{symbol}{price:,.2f}"


def send_price_alert(
    to_email: str,
    product_name: str,
    current_price: float,
    target_price: float,
    retailer: str,
    product_url: str,
    currency: str = "EUR",
    image_url: Optional[str] = None,
) -> Optional[str]:
    """
    Send a price drop alert email via Gmail SMTP.

    Returns 'sent' if successful, None otherwise.
    """
    settings = get_settings()

    if not settings.smtp_password:
        print("SMTP_PASSWORD not configured - skipping email alert")
        return None

    current_formatted = format_price(current_price, currency)
    target_formatted = format_price(target_price, currency)
    savings = target_price - current_price
    savings_formatted = format_price(savings, currency)

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
    </head>
    <body style="margin: 0; padding: 0; background-color: #0a0a0a; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color: #0a0a0a;">
            <tr>
                <td align="center" style="padding: 40px 20px;">
                    <table role="presentation" width="560" cellpadding="0" cellspacing="0" style="max-width: 560px; width: 100%;">

                        <!-- Logo -->
                        <tr>
                            <td align="center" style="padding-bottom: 32px;">
                                <table role="presentation" cellpadding="0" cellspacing="0">
                                    <tr>
                                        <td style="background: linear-gradient(135deg, #c8e600, #8fbf00); border-radius: 12px; width: 44px; height: 44px; text-align: center; vertical-align: middle; font-weight: 700; font-size: 18px; color: #0a0a0a;">
                                            P$
                                        </td>
                                        <td style="padding-left: 14px;">
                                            <span style="font-size: 22px; font-weight: 700; color: #f0f0f0; letter-spacing: -0.5px;">Price</span><span style="font-size: 22px; font-weight: 700; color: #c8e600; letter-spacing: -0.5px;">Spy</span>
                                        </td>
                                    </tr>
                                </table>
                            </td>
                        </tr>

                        <!-- Main Card -->
                        <tr>
                            <td style="background-color: #141414; border-radius: 16px; border: 1px solid #222; overflow: hidden;">

                                <!-- Header Banner -->
                                <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                                    <tr>
                                        <td style="background: linear-gradient(135deg, rgba(200, 230, 0, 0.15), rgba(200, 230, 0, 0.05)); padding: 28px 32px; border-bottom: 1px solid #222;">
                                            <p style="margin: 0; font-size: 13px; font-weight: 600; text-transform: uppercase; letter-spacing: 2px; color: #c8e600;">Deal Found</p>
                                            <h1 style="margin: 8px 0 0; font-size: 24px; font-weight: 600; color: #f0f0f0; line-height: 1.3;">{product_name}</h1>
                                        </td>
                                    </tr>
                                </table>

                                <!-- Product Image -->
                                {"" if not image_url else f'''
                                <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                                    <tr>
                                        <td align="center" style="padding: 28px 32px 0;">
                                            <img src="{image_url}" alt="{product_name}" style="max-width: 200px; max-height: 200px; border-radius: 12px; object-fit: contain;" />
                                        </td>
                                    </tr>
                                </table>
                                '''}

                                <!-- Price Section -->
                                <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                                    <tr>
                                        <td style="padding: 32px;">

                                            <!-- Current Price -->
                                            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color: #1a1a1a; border-radius: 12px; border: 1px solid #222;">
                                                <tr>
                                                    <td style="padding: 24px;">
                                                        <p style="margin: 0 0 6px; font-size: 12px; font-weight: 500; text-transform: uppercase; letter-spacing: 1.5px; color: #5a5a5a;">Current Price</p>
                                                        <p style="margin: 0; font-size: 36px; font-weight: 700; color: #c8e600; letter-spacing: -1px; line-height: 1.1;">{current_formatted}</p>
                                                    </td>
                                                </tr>
                                            </table>

                                            <!-- Target & Savings Row -->
                                            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top: 16px;">
                                                <tr>
                                                    <td width="50%" style="padding-right: 8px;">
                                                        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color: #1a1a1a; border-radius: 12px; border: 1px solid #222;">
                                                            <tr>
                                                                <td style="padding: 18px;">
                                                                    <p style="margin: 0 0 4px; font-size: 11px; font-weight: 500; text-transform: uppercase; letter-spacing: 1.5px; color: #5a5a5a;">Your Target</p>
                                                                    <p style="margin: 0; font-size: 18px; font-weight: 600; color: #a0a0a0; text-decoration: line-through;">{target_formatted}</p>
                                                                </td>
                                                            </tr>
                                                        </table>
                                                    </td>
                                                    <td width="50%" style="padding-left: 8px;">
                                                        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color: rgba(200, 230, 0, 0.08); border-radius: 12px; border: 1px solid rgba(200, 230, 0, 0.15);">
                                                            <tr>
                                                                <td style="padding: 18px;">
                                                                    <p style="margin: 0 0 4px; font-size: 11px; font-weight: 500; text-transform: uppercase; letter-spacing: 1.5px; color: #5a5a5a;">You Save</p>
                                                                    <p style="margin: 0; font-size: 18px; font-weight: 600; color: #c8e600;">{savings_formatted}</p>
                                                                </td>
                                                            </tr>
                                                        </table>
                                                    </td>
                                                </tr>
                                            </table>

                                            <!-- Source Info -->
                                            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top: 24px;">
                                                <tr>
                                                    <td>
                                                        <p style="margin: 0 0 4px; font-size: 12px; font-weight: 500; text-transform: uppercase; letter-spacing: 1.5px; color: #5a5a5a;">Source</p>
                                                        <p style="margin: 0; font-size: 16px; font-weight: 500; color: #f0f0f0;">{retailer}</p>
                                                    </td>
                                                </tr>
                                            </table>

                                            <!-- CTA Button -->
                                            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top: 28px;">
                                                <tr>
                                                    <td align="center">
                                                        <a href="{product_url}" style="display: inline-block; background: #c8e600; color: #0a0a0a; padding: 16px 40px; border-radius: 10px; font-size: 16px; font-weight: 600; text-decoration: none; letter-spacing: -0.2px;">View Deal</a>
                                                    </td>
                                                </tr>
                                            </table>

                                        </td>
                                    </tr>
                                </table>

                            </td>
                        </tr>

                        <!-- Footer -->
                        <tr>
                            <td style="padding: 28px 20px; text-align: center;">
                                <p style="margin: 0; font-size: 13px; color: #5a5a5a; line-height: 1.5;">
                                    You're receiving this because you set a price alert on PriceSpy.<br>
                                    Prices may vary and are subject to change.
                                </p>
                            </td>
                        </tr>

                    </table>
                </td>
            </tr>
        </table>
    </body>
    </html>
    """

    text_content = f"""DEAL FOUND - PriceSpy

{product_name}

Current Price at {retailer}: {current_formatted}
Your target price: {target_formatted}
You save: {savings_formatted}

View the deal: {product_url}

---
You're receiving this because you set a price alert on PriceSpy.
Prices may vary and are subject to change."""

    subject = f"Deal Found: {product_name} now {current_formatted}!"

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = settings.smtp_user
        msg["To"] = to_email

        msg.attach(MIMEText(text_content, "plain"))
        msg.attach(MIMEText(html_content, "html"))

        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_password)
            server.sendmail(settings.smtp_user, to_email, msg.as_string())

        print(f"Email alert sent to {to_email}")
        return "sent"
    except Exception as e:
        print(f"Failed to send email: {e}")
        return None


async def check_and_send_alert(
    product: dict,
    lowest_price: float,
    retailer: str,
    url: str,
    currency: str = "EUR"
) -> bool:
    """
    Check if price is below target and send alert if not sent recently.

    Returns True if alert was sent.
    """
    from .. import database
    from .currency import convert_price

    # Convert price to target currency before comparing
    target_currency = product.get("currency", "EUR")
    if currency != target_currency:
        converted = await convert_price(lowest_price, currency, target_currency)
        if converted is None:
            print(f"Could not convert {currency} to {target_currency} - skipping alert check")
            return False
        compare_price = converted
    else:
        compare_price = lowest_price

    # Check if price is below target
    if compare_price >= product["target_price"]:
        return False

    # Check if we already sent an alert recently (within 24 hours)
    recent_alert = await database.get_recent_alert(product["id"], hours=24)
    if recent_alert:
        return False

    # Send the alert
    email_id = send_price_alert(
        to_email=product["user_email"],
        product_name=product["name"],
        current_price=lowest_price,
        target_price=product["target_price"],
        retailer=retailer,
        product_url=url,
        currency=currency,
        image_url=product.get("image_url"),
    )

    if email_id:
        # Record the alert
        await database.add_alert_record(product["id"], lowest_price, retailer)
        return True

    return False
