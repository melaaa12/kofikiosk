"""
Database layer for KOFIKIOSK.
Uses plain sqlite3 (no ORM) so the schema stays easy to read and defend.
"""
import sqlite3
from pathlib import Path
from werkzeug.security import generate_password_hash

DB_PATH = Path(__file__).parent / "kofikiosk.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    display_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS menu_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    base_price REAL NOT NULL,
    image_url TEXT,
    is_available INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS sizes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    menu_item_id INTEGER NOT NULL REFERENCES menu_items(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    price_delta REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS addons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    menu_item_id INTEGER NOT NULL REFERENCES menu_items(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    price REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS promo_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    description TEXT,
    discount_type TEXT NOT NULL CHECK(discount_type IN ('percent','fixed')),
    value REAL NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS admins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_number TEXT NOT NULL UNIQUE,
    customer_name TEXT,
    order_type TEXT NOT NULL DEFAULT 'Dine-in',
    payment_method TEXT NOT NULL DEFAULT 'Cash',
    status TEXT NOT NULL DEFAULT 'Pending',
    discount_label TEXT,
    subtotal REAL NOT NULL,
    discount_amount REAL NOT NULL DEFAULT 0,
    tax_amount REAL NOT NULL DEFAULT 0,
    total REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    item_name TEXT NOT NULL,
    size_name TEXT,
    addons_summary TEXT,
    quantity INTEGER NOT NULL,
    unit_price REAL NOT NULL,
    line_total REAL NOT NULL,
    special_instructions TEXT
);
"""


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript(SCHEMA)
    conn.commit()
    _seed_db(conn)
    conn.close()


def _seed_db(conn):
    """Populate demo data on first run only (categories table empty)."""
    if conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] > 0:
        return

    categories = ["Coffee", "Non-Coffee", "Pastries"]
    cat_ids = {}
    for order, name in enumerate(categories):
        cur = conn.execute(
            "INSERT INTO categories (name, display_order) VALUES (?, ?)", (name, order)
        )
        cat_ids[name] = cur.lastrowid

    coffee_items = [
        ("Americano", "Bold espresso shots with hot water.", 89),
        ("Cappuccino", "Espresso, steamed milk, thick foam.", 109),
        ("Caffe Latte", "Espresso with smooth steamed milk.", 109),
        ("Spanish Latte", "Latte sweetened with condensed milk.", 119),
        ("Cafe Mocha", "Espresso, chocolate, steamed milk.", 119),
    ]
    noncoffee_items = [
        ("Matcha Latte", "Japanese green tea with steamed milk.", 129),
        ("Chocolate", "Rich hot or iced chocolate drink.", 109),
        ("Strawberry Milk", "Fresh strawberry blended milk drink.", 109),
    ]
    pastry_items = [
        ("Butter Croissant", "Flaky, buttery classic croissant.", 69),
        ("Blueberry Muffin", "Soft muffin loaded with blueberries.", 65),
        ("Choco Chip Cookie", "Chewy cookie with chocolate chips.", 45),
    ]

    drink_addons = [
        ("Extra Shot", 20),
        ("Vanilla Syrup", 15),
        ("Caramel Syrup", 15),
        ("Oat Milk", 25),
        ("Whipped Cream", 20),
    ]
    drink_sizes = [("Small", 0), ("Medium", 15), ("Large", 30)]

    def add_item(category_name, name, description, price, sizes, addons):
        cur = conn.execute(
            """INSERT INTO menu_items (category_id, name, description, base_price)
               VALUES (?,?,?,?)""",
            (cat_ids[category_name], name, description, price),
        )
        item_id = cur.lastrowid
        for size_name, delta in sizes:
            conn.execute(
                "INSERT INTO sizes (menu_item_id, name, price_delta) VALUES (?,?,?)",
                (item_id, size_name, delta),
            )
        for addon_name, addon_price in addons:
            conn.execute(
                "INSERT INTO addons (menu_item_id, name, price) VALUES (?,?,?)",
                (item_id, addon_name, addon_price),
            )

    for name, desc, price in coffee_items:
        add_item("Coffee", name, desc, price, drink_sizes, drink_addons)
    for name, desc, price in noncoffee_items:
        add_item("Non-Coffee", name, desc, price, drink_sizes, drink_addons)
    for name, desc, price in pastry_items:
        add_item("Pastries", name, desc, price, [("Regular", 0)], [])

    conn.execute(
        """INSERT INTO promo_codes (code, description, discount_type, value, is_active)
           VALUES (?,?,?,?,1)""",
        ("WELCOME10", "10% off for first-time customers", "percent", 10),
    )
    conn.execute(
        """INSERT INTO promo_codes (code, description, discount_type, value, is_active)
           VALUES (?,?,?,?,1)""",
        ("SAVE20", "PHP 20 off any order", "fixed", 20),
    )

    conn.execute("INSERT INTO settings (key, value) VALUES ('tax_rate', '0.12')")

    conn.execute(
        "INSERT INTO admins (username, password_hash) VALUES (?, ?)",
        ("admin", generate_password_hash("admin123")),
    )

    conn.commit()
