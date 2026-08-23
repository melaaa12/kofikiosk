"""
KOFIKIOSK - Web/App-Based Self-Order Kiosk System
Pre-thesis project (Application Development and Emerging Technologies)

Run with:  python index.py
Then open: http://127.0.0.1:5000
Admin panel: http://127.0.0.1:5000/admin/login  (admin / admin123)
"""
import csv
import io
import math
import os
import sqlite3
import uuid
from datetime import date, datetime, timedelta
from functools import wraps

from flask import (Flask, abort, flash, make_response, redirect,
                    render_template, request, session, url_for)
from werkzeug.security import check_password_hash

import db

app = Flask(__name__)
app.secret_key = os.environ.get("KOFIKIOSK_SECRET", "dev-secret-change-me")

with app.app_context():
    db.init_db()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def get_cart():
    return session.setdefault("cart", [])


def save_cart(cart):
    session["cart"] = cart
    session.modified = True


def cart_totals(cart, conn, discount_type=None, promo_code=None):
    """Server-side authoritative price computation. Never trust client-sent prices."""
    subtotal = sum(i["line_total"] for i in cart)
    discount_amount = 0.0
    discount_label = None

    if discount_type == "student":
        discount_amount = subtotal * 0.20
        discount_label = "Student Discount (20%)"
    elif discount_type == "senior_pwd":
        discount_amount = subtotal * 0.20
        discount_label = "Senior Citizen / PWD Discount (20%)"
    elif discount_type == "promo" and promo_code:
        promo = conn.execute(
            "SELECT * FROM promo_codes WHERE code=? AND is_active=1", (promo_code.upper(),)
        ).fetchone()
        if promo:
            if promo["discount_type"] == "percent":
                discount_amount = subtotal * (promo["value"] / 100)
            else:
                discount_amount = promo["value"]
            discount_label = f"Promo: {promo['code']}"

    discount_amount = round(min(discount_amount, subtotal), 2)
    taxable = subtotal - discount_amount
    tax_rate = float(get_setting(conn, "tax_rate", "0"))
    tax_amount = round(taxable * tax_rate, 2)
    total = round(taxable + tax_amount, 2)

    return {
        "subtotal": round(subtotal, 2),
        "discount_amount": discount_amount,
        "discount_label": discount_label,
        "tax_rate": tax_rate,
        "tax_amount": tax_amount,
        "total": total,
    }


def generate_order_number(conn):
    today_str = datetime.now().strftime("%Y%m%d")
    count = conn.execute(
        "SELECT COUNT(*) FROM orders WHERE order_number LIKE ?", (f"KK-{today_str}-%",)
    ).fetchone()[0]
    return f"KK-{today_str}-{count + 1:04d}"


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_id"):
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def parse_float(raw):
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    return val if math.isfinite(val) else None


def parse_nonneg_float(raw):
    val = parse_float(raw)
    return val if val is not None and val >= 0 else None


def parse_int(raw):
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def is_safe_next_url(path):
    return bool(path) and path.startswith("/") and not path.startswith("//") and "\\" not in path


VALID_ORDER_STATUSES = {"Pending", "Preparing", "Completed", "Cancelled"}


def period_start(period):
    if period == "weekly":
        return (date.today() - timedelta(days=7)).isoformat()
    if period == "monthly":
        return (date.today() - timedelta(days=30)).isoformat()
    return date.today().isoformat()


# ---------------------------------------------------------------------------
# Customer-facing routes
# ---------------------------------------------------------------------------
@app.route("/")
def menu():
    conn = db.get_db()
    categories = conn.execute(
        "SELECT * FROM categories ORDER BY display_order, name"
    ).fetchall()
    items = conn.execute(
        "SELECT * FROM menu_items WHERE is_available=1 ORDER BY name"
    ).fetchall()
    sizes = conn.execute("SELECT * FROM sizes").fetchall()
    addons = conn.execute("SELECT * FROM addons").fetchall()

    sizes_by_item, addons_by_item, items_by_cat = {}, {}, {}
    for s in sizes:
        sizes_by_item.setdefault(s["menu_item_id"], []).append(s)
    for a in addons:
        addons_by_item.setdefault(a["menu_item_id"], []).append(a)
    for it in items:
        items_by_cat.setdefault(it["category_id"], []).append(it)

    menu_data = {
        it["id"]: {
            "name": it["name"],
            "base_price": it["base_price"],
            "sizes": [
                {"id": s["id"], "name": s["name"], "price_delta": s["price_delta"]}
                for s in sizes_by_item.get(it["id"], [])
            ],
            "addons": [
                {"id": a["id"], "name": a["name"], "price": a["price"]}
                for a in addons_by_item.get(it["id"], [])
            ],
        }
        for it in items
    }
    cart_count = sum(i["quantity"] for i in get_cart())
    conn.close()
    return render_template(
        "customer/menu.html",
        categories=categories,
        items_by_cat=items_by_cat,
        menu_data=menu_data,
        cart_count=cart_count,
    )


@app.route("/cart/add", methods=["POST"])
def cart_add():
    conn = db.get_db()
    item_id = parse_int(request.form.get("item_id"))
    size_id = parse_int(request.form.get("size_id"))
    if item_id is None or size_id is None:
        conn.close()
        abort(400)
    addon_ids = request.form.getlist("addon_ids")
    qty = parse_int(request.form.get("quantity", 1))
    qty = max(1, min(20, qty if qty is not None else 1))
    instructions = request.form.get("instructions", "").strip()[:200]

    item = conn.execute(
        "SELECT * FROM menu_items WHERE id=? AND is_available=1", (item_id,)
    ).fetchone()
    size = conn.execute(
        "SELECT * FROM sizes WHERE id=? AND menu_item_id=?", (size_id, item_id)
    ).fetchone()
    if not item or not size:
        conn.close()
        abort(400)

    addons, addon_total = [], 0.0
    if addon_ids:
        placeholders = ",".join("?" * len(addon_ids))
        rows = conn.execute(
            f"SELECT * FROM addons WHERE id IN ({placeholders}) AND menu_item_id=?",
            (*addon_ids, item_id),
        ).fetchall()
        for a in rows:
            addons.append({"id": a["id"], "name": a["name"], "price": a["price"]})
            addon_total += a["price"]
    conn.close()

    unit_price = max(0.0, round(item["base_price"] + size["price_delta"] + addon_total, 2))
    line_total = round(unit_price * qty, 2)

    cart = get_cart()
    cart.append({
        "cart_id": uuid.uuid4().hex,
        "item_id": item["id"],
        "name": item["name"],
        "size_name": size["name"],
        "addons": addons,
        "quantity": qty,
        "unit_price": unit_price,
        "line_total": line_total,
        "instructions": instructions,
    })
    save_cart(cart)
    flash(f"Added {qty} x {item['name']} ({size['name']}) to your order.", "success")
    return redirect(url_for("menu"))


@app.route("/cart")
def cart_view():
    conn = db.get_db()
    cart = get_cart()
    totals = cart_totals(cart, conn, session.get("discount_type"), session.get("promo_code"))
    conn.close()
    return render_template(
        "customer/cart.html",
        cart=cart,
        totals=totals,
        discount_type=session.get("discount_type"),
        promo_code=session.get("promo_code", ""),
    )


@app.route("/cart/remove/<cart_id>", methods=["POST"])
def cart_remove(cart_id):
    save_cart([i for i in get_cart() if i["cart_id"] != cart_id])
    return redirect(url_for("cart_view"))


@app.route("/cart/update/<cart_id>", methods=["POST"])
def cart_update(cart_id):
    qty = parse_int(request.form.get("quantity", 1))
    qty = max(1, min(20, qty if qty is not None else 1))
    cart = get_cart()
    for i in cart:
        if i["cart_id"] == cart_id:
            i["quantity"] = qty
            i["line_total"] = round(i["unit_price"] * qty, 2)
    save_cart(cart)
    return redirect(url_for("cart_view"))


@app.route("/cart/discount", methods=["POST"])
def cart_discount():
    discount_type = request.form.get("discount_type") or None
    promo_code = request.form.get("promo_code", "").strip()
    session["discount_type"] = discount_type
    session["promo_code"] = promo_code
    if discount_type == "promo":
        conn = db.get_db()
        promo = conn.execute(
            "SELECT * FROM promo_codes WHERE code=? AND is_active=1", (promo_code.upper(),)
        ).fetchone()
        conn.close()
        if not promo:
            flash("Invalid or inactive promo code.", "error")
    return redirect(url_for("cart_view"))


@app.route("/cart/clear", methods=["POST"])
def cart_clear():
    session["cart"] = []
    session.pop("discount_type", None)
    session.pop("promo_code", None)
    return redirect(url_for("menu"))


@app.route("/checkout", methods=["GET", "POST"])
def checkout():
    cart = get_cart()
    if not cart:
        flash("Your cart is empty.", "error")
        return redirect(url_for("menu"))

    conn = db.get_db()
    if request.method == "POST":
        customer_name = request.form.get("customer_name", "Guest").strip() or "Guest"
        order_type = request.form.get("order_type", "Dine-in")
        payment_method = request.form.get("payment_method", "Cash")
        totals = cart_totals(cart, conn, session.get("discount_type"), session.get("promo_code"))
        order_number = generate_order_number(conn)

        cur = conn.execute(
            """INSERT INTO orders (order_number, customer_name, order_type, payment_method,
               status, discount_label, subtotal, discount_amount, tax_amount, total)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (order_number, customer_name, order_type, payment_method, "Pending",
             totals["discount_label"], totals["subtotal"], totals["discount_amount"],
             totals["tax_amount"], totals["total"]),
        )
        order_id = cur.lastrowid
        for i in cart:
            addons_summary = ", ".join(a["name"] for a in i["addons"]) if i["addons"] else None
            conn.execute(
                """INSERT INTO order_items (order_id, item_name, size_name, addons_summary,
                   quantity, unit_price, line_total, special_instructions)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (order_id, i["name"], i["size_name"], addons_summary, i["quantity"],
                 i["unit_price"], i["line_total"], i["instructions"] or None),
            )
        conn.commit()
        conn.close()

        session["cart"] = []
        session.pop("discount_type", None)
        session.pop("promo_code", None)
        flash("Order placed successfully!", "success")
        return redirect(url_for("receipt", order_number=order_number))

    totals = cart_totals(cart, conn, session.get("discount_type"), session.get("promo_code"))
    conn.close()
    return render_template("customer/checkout.html", cart=cart, totals=totals)


@app.route("/receipt/<order_number>")
def receipt(order_number):
    conn = db.get_db()
    order = conn.execute("SELECT * FROM orders WHERE order_number=?", (order_number,)).fetchone()
    if not order:
        conn.close()
        abort(404)
    items = conn.execute("SELECT * FROM order_items WHERE order_id=?", (order["id"],)).fetchall()
    conn.close()
    return render_template("customer/receipt.html", order=order, items=items)


# ---------------------------------------------------------------------------
# Admin auth
# ---------------------------------------------------------------------------
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        conn = db.get_db()
        admin = conn.execute("SELECT * FROM admins WHERE username=?", (username,)).fetchone()
        conn.close()
        if admin and check_password_hash(admin["password_hash"], password):
            session["admin_id"] = admin["id"]
            session["admin_username"] = admin["username"]
            next_url = request.args.get("next")
            if not is_safe_next_url(next_url):
                next_url = None
            return redirect(next_url or url_for("admin_dashboard"))
        flash("Invalid username or password.", "error")
    return render_template("admin/login.html")


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_id", None)
    session.pop("admin_username", None)
    return redirect(url_for("admin_login"))


# ---------------------------------------------------------------------------
# Admin dashboard
# ---------------------------------------------------------------------------
@app.route("/admin")
@admin_required
def admin_dashboard():
    conn = db.get_db()
    today = date.today().isoformat()
    today_sales = conn.execute(
        "SELECT COALESCE(SUM(total),0) s, COUNT(*) c FROM orders WHERE date(created_at)=?",
        (today,),
    ).fetchone()
    recent_orders = conn.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 10").fetchall()
    top_items = conn.execute(
        """SELECT item_name, SUM(quantity) qty, SUM(line_total) revenue
           FROM order_items GROUP BY item_name ORDER BY qty DESC LIMIT 5"""
    ).fetchall()
    conn.close()
    return render_template(
        "admin/dashboard.html",
        today_sales=today_sales,
        recent_orders=recent_orders,
        top_items=top_items,
    )


# ---------------------------------------------------------------------------
# Admin: categories
# ---------------------------------------------------------------------------
@app.route("/admin/categories", methods=["GET", "POST"])
@admin_required
def admin_categories():
    conn = db.get_db()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if name:
            conn.execute("INSERT OR IGNORE INTO categories (name) VALUES (?)", (name,))
            conn.commit()
    categories = conn.execute("SELECT * FROM categories ORDER BY display_order, name").fetchall()
    conn.close()
    return render_template("admin/categories.html", categories=categories)


@app.route("/admin/categories/<int:cat_id>/delete", methods=["POST"])
@admin_required
def admin_category_delete(cat_id):
    conn = db.get_db()
    conn.execute("DELETE FROM categories WHERE id=?", (cat_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_categories"))


# ---------------------------------------------------------------------------
# Admin: menu items, sizes, add-ons
# ---------------------------------------------------------------------------
@app.route("/admin/menu")
@admin_required
def admin_menu():
    conn = db.get_db()
    items = conn.execute(
        """SELECT mi.*, c.name AS category_name FROM menu_items mi
           JOIN categories c ON c.id = mi.category_id ORDER BY mi.name"""
    ).fetchall()
    conn.close()
    return render_template("admin/menu.html", items=items)


@app.route("/admin/menu/new", methods=["GET", "POST"])
@admin_required
def admin_menu_new():
    conn = db.get_db()
    categories = conn.execute("SELECT * FROM categories ORDER BY name").fetchall()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        category_id = parse_int(request.form.get("category_id"))
        description = request.form.get("description", "").strip()
        base_price = parse_nonneg_float(request.form.get("base_price"))
        image_url = request.form.get("image_url", "").strip()
        if not name or category_id is None or base_price is None:
            conn.close()
            flash("Please provide a valid name, category, and non-negative price.", "error")
            return render_template("admin/item_form.html", categories=categories, item=None,
                                    sizes=[], addons=[])
        cur = conn.execute(
            """INSERT INTO menu_items (category_id, name, description, base_price, image_url)
               VALUES (?,?,?,?,?)""",
            (category_id, name, description, base_price, image_url),
        )
        item_id = cur.lastrowid
        conn.execute(
            "INSERT INTO sizes (menu_item_id, name, price_delta) VALUES (?, 'Regular', 0)",
            (item_id,),
        )
        conn.commit()
        conn.close()
        flash("Menu item added. You can now add sizes and add-ons below.", "success")
        return redirect(url_for("admin_menu_edit", item_id=item_id))
    conn.close()
    return render_template("admin/item_form.html", categories=categories, item=None,
                            sizes=[], addons=[])


@app.route("/admin/menu/<int:item_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_menu_edit(item_id):
    conn = db.get_db()
    item = conn.execute("SELECT * FROM menu_items WHERE id=?", (item_id,)).fetchone()
    if not item:
        conn.close()
        abort(404)
    if request.method == "POST":
        category_id = parse_int(request.form.get("category_id"))
        name = request.form.get("name", "").strip()
        base_price = parse_nonneg_float(request.form.get("base_price"))
        if not name or category_id is None or base_price is None:
            flash("Please provide a valid name, category, and non-negative price.", "error")
        else:
            conn.execute(
                """UPDATE menu_items SET category_id=?, name=?, description=?, base_price=?,
                   image_url=?, is_available=? WHERE id=?""",
                (category_id, name,
                 request.form.get("description", "").strip(), base_price,
                 request.form.get("image_url", "").strip(),
                 1 if request.form.get("is_available") else 0, item_id),
            )
            conn.commit()
            flash("Menu item updated.", "success")

    categories = conn.execute("SELECT * FROM categories ORDER BY name").fetchall()
    item = conn.execute("SELECT * FROM menu_items WHERE id=?", (item_id,)).fetchone()
    sizes = conn.execute("SELECT * FROM sizes WHERE menu_item_id=?", (item_id,)).fetchall()
    addons = conn.execute("SELECT * FROM addons WHERE menu_item_id=?", (item_id,)).fetchall()
    conn.close()
    return render_template("admin/item_form.html", categories=categories, item=item,
                            sizes=sizes, addons=addons)


@app.route("/admin/menu/<int:item_id>/delete", methods=["POST"])
@admin_required
def admin_menu_delete(item_id):
    conn = db.get_db()
    conn.execute("DELETE FROM menu_items WHERE id=?", (item_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_menu"))


@app.route("/admin/menu/<int:item_id>/sizes/add", methods=["POST"])
@admin_required
def admin_size_add(item_id):
    conn = db.get_db()
    name = request.form.get("name", "").strip()
    price_delta = parse_float(request.form.get("price_delta", 0) or 0)
    if name and price_delta is not None:
        conn.execute(
            "INSERT INTO sizes (menu_item_id, name, price_delta) VALUES (?,?,?)",
            (item_id, name, price_delta),
        )
        conn.commit()
    else:
        flash("Size name is required and price must be a valid number.", "error")
    conn.close()
    return redirect(url_for("admin_menu_edit", item_id=item_id))


@app.route("/admin/sizes/<int:size_id>/delete", methods=["POST"])
@admin_required
def admin_size_delete(size_id):
    conn = db.get_db()
    row = conn.execute("SELECT menu_item_id FROM sizes WHERE id=?", (size_id,)).fetchone()
    if not row:
        conn.close()
        abort(404)
    conn.execute("DELETE FROM sizes WHERE id=?", (size_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_menu_edit", item_id=row["menu_item_id"]))


@app.route("/admin/menu/<int:item_id>/addons/add", methods=["POST"])
@admin_required
def admin_addon_add(item_id):
    conn = db.get_db()
    name = request.form.get("name", "").strip()
    price = parse_nonneg_float(request.form.get("price", 0) or 0)
    if name and price is not None:
        conn.execute(
            "INSERT INTO addons (menu_item_id, name, price) VALUES (?,?,?)",
            (item_id, name, price),
        )
        conn.commit()
    else:
        flash("Add-on name is required and price must be a non-negative number.", "error")
    conn.close()
    return redirect(url_for("admin_menu_edit", item_id=item_id))


@app.route("/admin/addons/<int:addon_id>/delete", methods=["POST"])
@admin_required
def admin_addon_delete(addon_id):
    conn = db.get_db()
    row = conn.execute("SELECT menu_item_id FROM addons WHERE id=?", (addon_id,)).fetchone()
    if not row:
        conn.close()
        abort(404)
    conn.execute("DELETE FROM addons WHERE id=?", (addon_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_menu_edit", item_id=row["menu_item_id"]))


# ---------------------------------------------------------------------------
# Admin: promo codes
# ---------------------------------------------------------------------------
@app.route("/admin/promos", methods=["GET", "POST"])
@admin_required
def admin_promos():
    conn = db.get_db()
    if request.method == "POST":
        code = request.form.get("code", "").strip().upper()
        discount_type = request.form.get("discount_type")
        value = parse_nonneg_float(request.form.get("value"))
        description = request.form.get("description", "").strip()
        if not code or discount_type not in ("percent", "fixed") or value is None:
            flash("Please provide a valid code, discount type, and non-negative value.", "error")
        elif discount_type == "percent" and value > 100:
            flash("Percent discount cannot exceed 100.", "error")
        else:
            try:
                conn.execute(
                    """INSERT INTO promo_codes (code, description, discount_type, value, is_active)
                       VALUES (?,?,?,?,1)""",
                    (code, description, discount_type, value),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                flash(f"Promo code '{code}' already exists.", "error")
    promos = conn.execute("SELECT * FROM promo_codes ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("admin/promos.html", promos=promos)


@app.route("/admin/promos/<int:promo_id>/toggle", methods=["POST"])
@admin_required
def admin_promo_toggle(promo_id):
    conn = db.get_db()
    conn.execute("UPDATE promo_codes SET is_active = 1 - is_active WHERE id=?", (promo_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_promos"))


@app.route("/admin/promos/<int:promo_id>/delete", methods=["POST"])
@admin_required
def admin_promo_delete(promo_id):
    conn = db.get_db()
    conn.execute("DELETE FROM promo_codes WHERE id=?", (promo_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_promos"))


# ---------------------------------------------------------------------------
# Admin: orders
# ---------------------------------------------------------------------------
@app.route("/admin/orders")
@admin_required
def admin_orders():
    status = request.args.get("status")
    conn = db.get_db()
    if status:
        orders = conn.execute(
            "SELECT * FROM orders WHERE status=? ORDER BY id DESC", (status,)
        ).fetchall()
    else:
        orders = conn.execute("SELECT * FROM orders ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("admin/orders.html", orders=orders, status=status)


@app.route("/admin/orders/<int:order_id>")
@admin_required
def admin_order_detail(order_id):
    conn = db.get_db()
    order = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not order:
        conn.close()
        abort(404)
    items = conn.execute("SELECT * FROM order_items WHERE order_id=?", (order_id,)).fetchall()
    conn.close()
    return render_template("admin/order_detail.html", order=order, items=items)


@app.route("/admin/orders/<int:order_id>/status", methods=["POST"])
@admin_required
def admin_order_status(order_id):
    new_status = request.form.get("status")
    if new_status not in VALID_ORDER_STATUSES:
        abort(400)
    conn = db.get_db()
    conn.execute("UPDATE orders SET status=? WHERE id=?", (new_status, order_id))
    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for("admin_orders"))


# ---------------------------------------------------------------------------
# Admin: sales reports
# ---------------------------------------------------------------------------
@app.route("/admin/reports")
@admin_required
def admin_reports():
    period = request.args.get("period", "daily")
    start = period_start(period)
    conn = db.get_db()
    sales_by_day = conn.execute(
        """SELECT date(created_at) d, SUM(total) revenue, COUNT(*) orders
           FROM orders WHERE date(created_at) >= ? GROUP BY d ORDER BY d""",
        (start,),
    ).fetchall()
    best_sellers = conn.execute(
        """SELECT oi.item_name, SUM(oi.quantity) qty, SUM(oi.line_total) revenue
           FROM order_items oi JOIN orders o ON o.id = oi.order_id
           WHERE date(o.created_at) >= ? GROUP BY oi.item_name ORDER BY qty DESC LIMIT 10""",
        (start,),
    ).fetchall()
    totals = conn.execute(
        "SELECT COALESCE(SUM(total),0) revenue, COUNT(*) orders FROM orders WHERE date(created_at) >= ?",
        (start,),
    ).fetchone()
    conn.close()
    return render_template(
        "admin/reports.html",
        period=period,
        sales_by_day=sales_by_day,
        best_sellers=best_sellers,
        totals=totals,
    )


@app.route("/admin/reports/export")
@admin_required
def admin_reports_export():
    period = request.args.get("period", "daily")
    start = period_start(period)
    conn = db.get_db()
    orders = conn.execute(
        "SELECT * FROM orders WHERE date(created_at) >= ? ORDER BY created_at", (start,)
    ).fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Order Number", "Date", "Customer", "Type", "Payment", "Status",
                      "Subtotal", "Discount", "Tax", "Total"])
    for o in orders:
        writer.writerow([o["order_number"], o["created_at"], o["customer_name"], o["order_type"],
                          o["payment_method"], o["status"], o["subtotal"], o["discount_amount"],
                          o["tax_amount"], o["total"]])

    resp = make_response(output.getvalue())
    resp.headers["Content-Type"] = "text/csv"
    resp.headers["Content-Disposition"] = f"attachment; filename=kofikiosk_sales_{period}.csv"
    return resp


# ---------------------------------------------------------------------------
# Admin: settings
# ---------------------------------------------------------------------------
@app.route("/admin/settings", methods=["GET", "POST"])
@admin_required
def admin_settings():
    conn = db.get_db()
    if request.method == "POST":
        tax_rate = parse_nonneg_float(request.form.get("tax_rate", "0"))
        if tax_rate is None or tax_rate > 1:
            flash("Tax rate must be a number between 0 and 1 (e.g. 0.12 for 12%).", "error")
        else:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('tax_rate', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(tax_rate),),
            )
            conn.commit()
            flash("Settings updated.", "success")
    tax_rate = get_setting(conn, "tax_rate", "0")
    conn.close()
    return render_template("admin/settings.html", tax_rate=tax_rate)


if __name__ == "__main__":
    app.run(debug=True)
