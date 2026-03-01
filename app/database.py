import aiosqlite
import os
from typing import Optional, List
from datetime import datetime
from .config import get_settings

# Database connection
_connection: Optional[aiosqlite.Connection] = None


async def get_connection() -> aiosqlite.Connection:
    """Get or create database connection."""
    global _connection
    if _connection is None:
        settings = get_settings()
        # Ensure the data directory exists
        db_dir = os.path.dirname(settings.database_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
        _connection = await aiosqlite.connect(settings.database_path)
        _connection.row_factory = aiosqlite.Row
    return _connection


async def init_db():
    """Initialize the database with required tables."""
    conn = await get_connection()

    # Create products table
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            search_query TEXT NOT NULL,
            category TEXT DEFAULT 'electronics',
            region TEXT DEFAULT 'eu',
            size TEXT,
            color TEXT,
            brand TEXT,
            model TEXT,
            storage TEXT,
            material TEXT,
            target_price REAL NOT NULL,
            currency TEXT DEFAULT 'EUR',
            user_email TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            image_url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Add image_url column if it doesn't exist (for existing databases)
    try:
        await conn.execute("ALTER TABLE products ADD COLUMN image_url TEXT")
        await conn.commit()
    except Exception:
        pass  # Column already exists

    # Add sort_order column if it doesn't exist
    try:
        await conn.execute("ALTER TABLE products ADD COLUMN sort_order INTEGER DEFAULT 0")
        await conn.commit()
    except Exception:
        pass  # Column already exists

    # Create price_history table
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            retailer TEXT NOT NULL,
            price REAL NOT NULL,
            currency TEXT DEFAULT 'USD',
            url TEXT NOT NULL,
            scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Create alerts_sent table
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS alerts_sent (
            id INTEGER PRIMARY KEY,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            price REAL NOT NULL,
            retailer TEXT NOT NULL,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Create index
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_price_history_product
        ON price_history(product_id, scraped_at DESC)
    """)

    # Create excluded_sources table
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS excluded_sources (
            id INTEGER PRIMARY KEY,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            retailer TEXT NOT NULL,
            excluded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(product_id, retailer)
        )
    """)

    # Create source_status table to track scraping success/failure
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS source_status (
            id INTEGER PRIMARY KEY,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            retailer TEXT NOT NULL,
            success INTEGER NOT NULL DEFAULT 1,
            error_message TEXT,
            last_checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(product_id, retailer)
        )
    """)

    # Create users table
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Seed user from env vars
    settings = get_settings()
    if settings.auth_email and settings.auth_password_hash:
        await conn.execute(
            """
            INSERT OR IGNORE INTO users (email, password_hash)
            VALUES (?, ?)
            """,
            (settings.auth_email, settings.auth_password_hash)
        )

    # Enable foreign keys
    await conn.execute("PRAGMA foreign_keys = ON")

    await conn.commit()


async def close_db():
    """Close database connection."""
    global _connection
    if _connection:
        await _connection.close()
        _connection = None


# Product CRUD operations
async def create_product(
    name: str,
    search_query: str,
    target_price: float,
    user_email: str,
    category: str = "electronics",
    region: str = "eu",
    size: Optional[str] = None,
    color: Optional[str] = None,
    brand: Optional[str] = None,
    model: Optional[str] = None,
    storage: Optional[str] = None,
    material: Optional[str] = None,
    currency: str = "EUR",
    image_url: Optional[str] = None
) -> int:
    conn = await get_connection()
    cursor = await conn.execute(
        """
        INSERT INTO products (name, search_query, category, region, size, color, brand, model, storage, material, target_price, currency, user_email, image_url)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (name, search_query, category, region, size, color, brand, model, storage, material, target_price, currency, user_email, image_url)
    )
    await conn.commit()
    return cursor.lastrowid


async def get_product(product_id: int) -> Optional[dict]:
    conn = await get_connection()
    cursor = await conn.execute(
        "SELECT * FROM products WHERE id = ?",
        (product_id,)
    )
    row = await cursor.fetchone()
    if row:
        return dict(row)
    return None


async def get_all_products(active_only: bool = False) -> List[dict]:
    conn = await get_connection()
    if active_only:
        cursor = await conn.execute(
            "SELECT * FROM products WHERE is_active = 1 ORDER BY sort_order ASC, created_at DESC"
        )
    else:
        cursor = await conn.execute(
            "SELECT * FROM products ORDER BY sort_order ASC, created_at DESC"
        )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def reorder_products(product_ids: List[int]) -> None:
    """Set sort_order for products based on the order of IDs provided."""
    conn = await get_connection()
    for index, product_id in enumerate(product_ids):
        await conn.execute(
            "UPDATE products SET sort_order = ? WHERE id = ?",
            (index, product_id)
        )
    await conn.commit()


async def update_product(product_id: int, **kwargs) -> bool:
    if not kwargs:
        return False

    conn = await get_connection()

    # Build dynamic update query
    set_clauses = []
    values = []
    for key, value in kwargs.items():
        set_clauses.append(f"{key} = ?")
        values.append(value)

    values.append(product_id)
    query = f"UPDATE products SET {', '.join(set_clauses)} WHERE id = ?"

    cursor = await conn.execute(query, values)
    await conn.commit()
    return cursor.rowcount > 0


async def delete_product(product_id: int) -> bool:
    conn = await get_connection()
    cursor = await conn.execute(
        "DELETE FROM products WHERE id = ?",
        (product_id,)
    )
    await conn.commit()
    return cursor.rowcount > 0


# Price history operations
async def add_price_record(
    product_id: int,
    retailer: str,
    price: float,
    url: str,
    currency: str = "USD"
) -> int:
    conn = await get_connection()
    cursor = await conn.execute(
        """
        INSERT INTO price_history (product_id, retailer, price, currency, url)
        VALUES (?, ?, ?, ?, ?)
        """,
        (product_id, retailer, price, currency, url)
    )
    await conn.commit()
    return cursor.lastrowid


async def get_price_history(product_id: int, limit: int = 50) -> List[dict]:
    conn = await get_connection()
    cursor = await conn.execute(
        """
        SELECT * FROM price_history
        WHERE product_id = ?
        ORDER BY scraped_at DESC
        LIMIT ?
        """,
        (product_id, limit)
    )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def get_lowest_price(product_id: int) -> Optional[dict]:
    conn = await get_connection()
    cursor = await conn.execute(
        """
        SELECT * FROM price_history
        WHERE product_id = ?
        ORDER BY price ASC
        LIMIT 1
        """,
        (product_id,)
    )
    row = await cursor.fetchone()
    if row:
        return dict(row)
    return None


async def get_latest_prices(product_id: int) -> List[dict]:
    """Get the most recent price from each retailer for a product."""
    conn = await get_connection()
    # SQLite doesn't have DISTINCT ON, so use a subquery with GROUP BY
    cursor = await conn.execute(
        """
        SELECT ph.*
        FROM price_history ph
        INNER JOIN (
            SELECT retailer, MAX(scraped_at) as max_scraped_at
            FROM price_history
            WHERE product_id = ?
            GROUP BY retailer
        ) latest ON ph.retailer = latest.retailer AND ph.scraped_at = latest.max_scraped_at
        WHERE ph.product_id = ?
        ORDER BY ph.price ASC
        """,
        (product_id, product_id)
    )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


# Alert operations
async def add_alert_record(product_id: int, price: float, retailer: str) -> int:
    conn = await get_connection()
    cursor = await conn.execute(
        """
        INSERT INTO alerts_sent (product_id, price, retailer)
        VALUES (?, ?, ?)
        """,
        (product_id, price, retailer)
    )
    await conn.commit()
    return cursor.lastrowid


async def get_recent_alert(product_id: int, hours: int = 24) -> Optional[dict]:
    """Check if an alert was sent recently for this product."""
    conn = await get_connection()
    cursor = await conn.execute(
        """
        SELECT * FROM alerts_sent
        WHERE product_id = ?
        AND sent_at > datetime('now', ?)
        ORDER BY sent_at DESC
        LIMIT 1
        """,
        (product_id, f'-{hours} hours')
    )
    row = await cursor.fetchone()
    if row:
        return dict(row)
    return None


# Excluded sources operations
async def exclude_source(product_id: int, retailer: str) -> bool:
    """Exclude a retailer from price tracking for a product."""
    conn = await get_connection()
    try:
        await conn.execute(
            "INSERT OR IGNORE INTO excluded_sources (product_id, retailer) VALUES (?, ?)",
            (product_id, retailer)
        )
        await conn.commit()
        return True
    except Exception:
        return False


async def include_source(product_id: int, retailer: str) -> bool:
    """Re-include a previously excluded retailer."""
    conn = await get_connection()
    cursor = await conn.execute(
        "DELETE FROM excluded_sources WHERE product_id = ? AND retailer = ?",
        (product_id, retailer)
    )
    await conn.commit()
    return cursor.rowcount > 0


async def get_excluded_sources(product_id: int) -> List[str]:
    """Get list of excluded retailer names for a product."""
    conn = await get_connection()
    cursor = await conn.execute(
        "SELECT retailer FROM excluded_sources WHERE product_id = ?",
        (product_id,)
    )
    rows = await cursor.fetchall()
    return [row['retailer'] for row in rows]


async def is_source_excluded(product_id: int, retailer: str) -> bool:
    """Check if a retailer is excluded for a product."""
    conn = await get_connection()
    cursor = await conn.execute(
        "SELECT 1 FROM excluded_sources WHERE product_id = ? AND retailer = ?",
        (product_id, retailer)
    )
    row = await cursor.fetchone()
    return row is not None


# Source status operations
async def update_source_status(
    product_id: int,
    retailer: str,
    success: bool,
    error_message: Optional[str] = None
) -> bool:
    """Update the scraping status for a source."""
    conn = await get_connection()
    await conn.execute(
        """
        INSERT INTO source_status (product_id, retailer, success, error_message, last_checked_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(product_id, retailer) DO UPDATE SET
            success = excluded.success,
            error_message = excluded.error_message,
            last_checked_at = CURRENT_TIMESTAMP
        """,
        (product_id, retailer, 1 if success else 0, error_message)
    )
    await conn.commit()
    return True


async def get_source_statuses(product_id: int) -> dict:
    """Get scraping status for all sources of a product. Returns dict[retailer] = {success, error_message, last_checked_at}."""
    conn = await get_connection()
    cursor = await conn.execute(
        "SELECT retailer, success, error_message, last_checked_at FROM source_status WHERE product_id = ?",
        (product_id,)
    )
    rows = await cursor.fetchall()
    return {
        row['retailer']: {
            'success': bool(row['success']),
            'error_message': row['error_message'],
            'last_checked_at': row['last_checked_at']
        }
        for row in rows
    }


# User operations
async def get_user_by_email(email: str) -> Optional[dict]:
    conn = await get_connection()
    cursor = await conn.execute(
        "SELECT * FROM users WHERE email = ?",
        (email,)
    )
    row = await cursor.fetchone()
    if row:
        return dict(row)
    return None
