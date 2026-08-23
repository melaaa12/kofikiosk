# KOFIKIOSK: Web/App-Based Self-Order Kiosk System

Pre-thesis project proposal — Application Development and Emerging Technologies (BSCS 3rd Year)
Christ the King College, Calbayog City

Built with **Python (Flask)** and **SQLite**, matching the proposal's stated programming language and objectives.

## 1. What's included

```
pre-thesis/
├── index.py                 # Flask app: all routes (customer + admin)
├── db.py                    # SQLite schema + demo data seeding
├── requirements.txt         # Python dependencies (just Flask)
├── kofikiosk.db              # created automatically on first run
├── static/
│   └── css/style.css        # all styling (kiosk + admin panel)
└── templates/
    ├── customer/            # menu, cart, checkout, receipt
    └── admin/                # login, dashboard, menu mgmt, orders, reports
```

## 2. How to run it (Windows PowerShell)

You already have a virtual environment and Flask installed at
`pre-thesis\venv`. To run the app:

```powershell
cd "C:\Users\ASUS\OneDrive\Desktop\pre-thesis"
.\venv\Scripts\Activate.ps1
python index.py
```

Then open **http://127.0.0.1:5000** in your browser — that's the customer
kiosk screen. Open it on your phone too (same Wi-Fi, use your PC's local
IP instead of 127.0.0.1) to demo the "web/app-based" angle.

Admin panel: **http://127.0.0.1:5000/admin/login**
Default login: `admin` / `admin123` — change this before your defense
(see Settings note below; for now you can edit the seed password in
`db.py` and delete `kofikiosk.db` to reseed).

If you ever set up the project on a new machine, just repeat:
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python index.py
```

The database file `kofikiosk.db` is created and seeded with sample
categories, drinks, pastries, sizes, add-ons, and one admin account the
first time you run the app. Delete that file and re-run to reset
everything back to the demo data.

## 3. Database design

SQLite, 9 tables, created by `db.py`:

| Table | Purpose |
|---|---|
| `categories` | Coffee / Non-Coffee / Pastries, etc. |
| `menu_items` | Name, description, base price, availability |
| `sizes` | Per-item size options with a price add-on (e.g. Large +₱30) |
| `addons` | Per-item extras (Extra Shot, Oat Milk, syrups...) |
| `promo_codes` | Percent or fixed-amount discount codes |
| `settings` | Key/value config — currently just `tax_rate` |
| `admins` | Admin accounts (hashed passwords via Werkzeug) |
| `orders` | One row per placed order, with computed totals |
| `order_items` | Line items belonging to each order (snapshotted, so later menu edits don't change historical receipts) |

Order pricing is **always recomputed server-side** from the database
(`cart_totals()` in `index.py`) — the customer's browser never gets to
tell the server what something costs, even though it shows a live preview
locally for UX. This is a good point to bring up in your defense when
discussing accuracy/security.

## 4. Feature walkthrough → thesis objectives

| Objective in your proposal | Where it's implemented |
|---|---|
| Browse menu categories, select items, customize (size, add-ons, quantity) | `/` (`templates/customer/menu.html`) — category tabs + a customize modal driven by `menu.html`'s embedded `MENU_DATA` JS object |
| Automated computation: subtotal, discounts/promo, tax, live total | `cart_totals()` in `index.py`; live preview in the modal's JS, authoritative calc on the server at every step |
| Order summary & receipt generation | `/cart`, `/checkout`, `/receipt/<order_number>` |
| Payment integration | Checkout page lets the customer pick Cash-on-counter or GCash (confirmed manually for this prototype — see extension notes below) |
| Admin-side menu/price management | `/admin/menu`, `/admin/categories`, size & add-on editors, all without touching code |
| Evaluate functionality/usability/reliability (ISO/IEC 25010) | Not code — this is your testing chapter. See Section 6 below for a concrete plan. |
| Measure effect on order time & accuracy vs. manual ordering | Not code — see Section 6; the app's timestamps (`orders.created_at`) and error-free computation give you data to compare against a manual/simulated baseline. |

Discounts implemented: Student (20%), Senior Citizen/PWD (20%), and promo
codes (`WELCOME10`, `SAVE20` seeded — manage more under
`/admin/promos`). Tax rate is configurable under `/admin/settings`
(defaults to 12%, set to 0 if you're presenting a non-VAT scenario).

Sales reports (`/admin/reports`) cover daily/weekly/monthly revenue,
order counts, and best-selling items, with CSV export — this satisfies
"Generates Sales Reports" from your Pros list.

## 5. Suggested demo script for your presentation

1. Open the kiosk screen, browse categories, customize a Cappuccino
   (size + 2 add-ons), add to cart.
2. Show the cart updating totals live, apply a promo code (`WELCOME10`).
3. Checkout as a guest, show the generated order number and printable
   receipt.
4. Switch to the admin panel — show the new order appear on the
   dashboard and in Orders, walk through updating its status
   (Pending → Preparing → Completed).
5. Add a brand-new menu item live (e.g. a seasonal drink), go back to
   the kiosk screen, and show it immediately available — this is your
   "Easy Menu Management" pro point in action.
6. Show the Sales Reports tab with the order you just placed reflected
   in the daily total.

## 6. For your ISO/IEC 25010 and performance objectives (documentation, not code)

These two specific objectives are evaluation activities you run and write
up, not something that lives in the codebase:

- **Functionality/usability/reliability**: write a short test-case
  checklist per ISO 25010 characteristic (e.g. functional
  completeness — every menu item can be ordered; usability — a
  first-time user completes an order in under N taps; reliability —
  the app doesn't lose the cart if the tab is refreshed since it's
  stored server-side in the session). Have a few classmates or
  relatives use the kiosk and time/survey them.
- **Order time & accuracy vs. manual ordering**: time a few volunteers
  placing the same sample order (a) by talking to a "cashier" doing
  mental math and (b) using KOFIKIOSK, and compare. Because totals are
  computed by `cart_totals()`, kiosk orders will have zero arithmetic
  error by construction — your write-up can cite the specific function
  as the reason.

## 7. Where to extend if you have time before defense

- **Real payment gateway**: the checkout form's `payment_method` field
  is the integration point — swap the "confirmed at counter" flash
  message in `checkout()` (`index.py`) for a call to a gateway SDK
  (e.g. PayMongo, GCash API) before saving the order as paid.
- **Change the admin password**: currently seeded once in `db.py`. A
  clean addition would be an `/admin/change-password` route using
  `werkzeug.security.generate_password_hash`.
- **Kitchen display view**: `/admin/orders?status=Pending` already
  gives you a filtered queue — you could make a stripped-down
  auto-refreshing version for a kitchen screen.
- **Images**: menu items support an `image_url` field already; point it
  at any hosted image to replace the placeholder cup icon.

## 8. Notes for the encoder / group members

- All prices are stored as `REAL` (float) in SQLite for simplicity;
  this is fine for a prototype but if this becomes a real product,
  switch to storing centavos as integers to avoid floating-point
  rounding issues.
- The cart lives in the Flask session (a signed cookie), not the
  database, until checkout — that's why refreshing the kiosk page
  doesn't lose your order, but clearing cookies does.
- No external JS/CSS frameworks are used — everything is vanilla
  HTML/CSS/JS plus Jinja2 templates, so there's nothing extra to
  install beyond Flask itself.
