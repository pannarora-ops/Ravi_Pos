import os
import sqlite3
from datetime import datetime, timezone
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": [
    "https://pannarora-ops.github.io",
    "https://raviconfectionery.netlify.app"
]}})

DB_FILE = os.environ.get("ONLINE_ORDERS_DB", "online_orders.db")
API_KEY = os.environ.get("API_KEY", "").strip()

def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS online_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_uid TEXT UNIQUE NOT NULL,
            order_date TEXT,
            customer_name TEXT,
            customer_phone TEXT,
            address TEXT,
            items_summary TEXT,
            total_amount REAL,
            payment_mode TEXT,
            status TEXT DEFAULT 'PENDING',
            delivered_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS store_products (
            id INTEGER PRIMARY KEY,
            barcode TEXT,
            name TEXT NOT NULL,
            category TEXT DEFAULT 'General',
            sub_category TEXT DEFAULT '',
            primary_unit TEXT DEFAULT 'BOX',
            secondary_unit TEXT DEFAULT 'PCS',
            conversion_factor REAL DEFAULT 1,
            sale_rate REAL DEFAULT 0,
            stock REAL DEFAULT 0,
            gst_rate REAL DEFAULT 0,
            active INTEGER DEFAULT 1,
            updated_at TEXT
        )
    """)
    conn.commit()
    conn.close()

def authorized():
    return bool(API_KEY) and request.headers.get("X-API-Key", "") == API_KEY

def new_order_uid():
    now = datetime.now().strftime("%Y%m%d%H%M%S")
    return f"WEB-{now}-{os.urandom(3).hex().upper()}"

@app.get("/")
def root():
    return jsonify({
        "service": "Ravi Confectionery Online Orders API",
        "status": "success"
    })

@app.post("/api/save_order")
def save_order():
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()
    phone = str(data.get("phone", "")).strip()
    address = str(data.get("address", "")).strip()
    items = str(data.get("items", "")).strip()
    mode = str(data.get("mode", "COD")).strip() or "COD"

    try:
        total = float(data.get("total", 0))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Invalid total"}), 400

    if not name or not phone or not address or not items:
        return jsonify({"status": "error", "message": "Missing required order fields"}), 400

    uid = new_order_uid()
    now = datetime.now(timezone.utc).isoformat()
    conn = db()
    conn.execute("""
        INSERT INTO online_orders
        (order_uid, order_date, customer_name, customer_phone, address,
         items_summary, total_amount, payment_mode, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING')
    """, (uid, now, name, phone, address, items, total, mode))
    conn.commit()
    conn.close()

    return jsonify({
        "status": "success",
        "order_id": uid,
        "message": "Order saved successfully"
    }), 201

@app.get("/api/orders")
def get_orders():
    if not authorized():
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    conn = db()
    rows = conn.execute("""
        SELECT order_uid AS id, order_date AS created_at, customer_name,
               customer_phone AS phone, address, items_summary,
               total_amount AS total, payment_mode AS mode, status, delivered_at
        FROM online_orders
        ORDER BY id DESC
    """).fetchall()
    conn.close()
    return jsonify({"status": "success", "orders": [dict(r) for r in rows]})

@app.post("/api/orders/<order_uid>/delivered")
def mark_delivered(order_uid):
    if not authorized():
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    now = datetime.now(timezone.utc).isoformat()
    conn = db()
    cur = conn.execute("""
        UPDATE online_orders
        SET status='DELIVERED', delivered_at=?
        WHERE order_uid=?
    """, (now, order_uid))
    conn.commit()
    changed = cur.rowcount
    conn.close()

    if not changed:
        return jsonify({"status": "error", "message": "Order not found"}), 404
    return jsonify({"status": "success", "message": "Order marked delivered"})

@app.get("/api/products")
def get_products():
    # Public read: only products marked Active by the POS are exposed.
    conn = db()
    rows = conn.execute("""
        SELECT id, barcode, name, category, sub_category, primary_unit,
               secondary_unit, conversion_factor, sale_rate, stock, gst_rate
        FROM store_products
        WHERE active=1 AND stock > 0
        ORDER BY category, name
    """).fetchall()
    conn.close()
    return jsonify({"status": "success", "products": [dict(r) for r in rows]})

@app.post("/api/products/sync")
def sync_products():
    if not authorized():
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    payload = request.get_json(silent=True) or {}
    products = payload.get("products")
    if not isinstance(products, list):
        return jsonify({"status": "error", "message": "products must be a list"}), 400

    now = datetime.now(timezone.utc).isoformat()
    conn = db()
    for p in products:
        try:
            pid = int(p["id"])
            conn.execute("""
                INSERT INTO store_products
                (id, barcode, name, category, sub_category, primary_unit,
                 secondary_unit, conversion_factor, sale_rate, stock, gst_rate,
                 active, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  barcode=excluded.barcode, name=excluded.name,
                  category=excluded.category, sub_category=excluded.sub_category,
                  primary_unit=excluded.primary_unit, secondary_unit=excluded.secondary_unit,
                  conversion_factor=excluded.conversion_factor,
                  sale_rate=excluded.sale_rate, stock=excluded.stock,
                  gst_rate=excluded.gst_rate, active=excluded.active,
                  updated_at=excluded.updated_at
            """, (
                pid, str(p.get("barcode", "")), str(p.get("name", "")),
                str(p.get("category", "General")), str(p.get("sub_category", "")),
                str(p.get("primary_unit", "BOX")), str(p.get("secondary_unit", "PCS")),
                float(p.get("conversion_factor", 1)), float(p.get("sale_rate", 0)),
                float(p.get("stock", 0)), float(p.get("gst_rate", 0)),
                1 if float(p.get("stock", 0) or 0) > 0 else 0, now
            ))
        except (KeyError, TypeError, ValueError) as exc:
            conn.close()
            return jsonify({"status": "error", "message": f"Invalid product: {exc}"}), 400

    conn.commit()
    count = len(products)
    conn.close()
    return jsonify({"status": "success", "count": count})

init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
