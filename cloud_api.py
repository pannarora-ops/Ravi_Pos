import os
import sqlite3
import uuid
from datetime import datetime

from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)

# The online store is a static website, so SAVE_ORDER is intentionally
# public. Reading orders and changing delivery status remain protected
# by API_KEY.
ALLOWED_ORIGINS = [
    "https://pannarora-ops.github.io",
    "https://raviconfectionery.netlify.app",
]

CORS(
    app,
    resources={
        r"/api/*": {
            "origins": ALLOWED_ORIGINS,
            "methods": ["GET", "POST", "OPTIONS"],
            "allow_headers": ["Content-Type", "X-API-Key"],
        }
    },
)

DB_PATH = os.environ.get("DB_PATH", "online_orders.db")
API_KEY = os.environ.get("API_KEY", "").strip()


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS online_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_uid TEXT UNIQUE NOT NULL,
            order_date TEXT NOT NULL,
            customer_name TEXT NOT NULL,
            customer_phone TEXT NOT NULL,
            address TEXT NOT NULL,
            items_summary TEXT NOT NULL,
            total_amount REAL NOT NULL DEFAULT 0,
            payment_mode TEXT DEFAULT 'Online',
            status TEXT DEFAULT 'PENDING',
            delivered_at TEXT DEFAULT ''
        )
    """)

    conn.commit()
    conn.close()


def authorized():
    """
    Protected desktop/POS endpoints use X-API-Key.
    API_KEY must be configured in Render Environment Variables.
    """
    if not API_KEY:
        return False

    supplied_key = request.headers.get("X-API-Key", "").strip()
    return supplied_key == API_KEY


def error(message, status=400):
    return jsonify({
        "status": "error",
        "message": message
    }), status


@app.get("/")
def home():
    return jsonify({
        "status": "success",
        "service": "Ravi Confectionery Online Orders API"
    })


@app.post("/api/save_order")
def save_order():
    """
    Public endpoint used by the GitHub Pages online store.

    DO NOT put the private POS API_KEY in index.html.
    WhatsApp ordering remains independent of this API.
    """
    try:
        data = request.get_json(silent=True) or {}

        name = str(data.get("name", "")).strip()
        phone = str(data.get("phone", "")).strip()
        address = str(data.get("address", "")).strip()
        items = str(data.get("items", "")).strip()
        mode = str(data.get("mode", "Online")).strip() or "Online"

        try:
            total = float(data.get("total", 0) or 0)
        except (TypeError, ValueError):
            return error("Invalid total amount", 400)

        if total < 0:
            return error("Invalid total amount", 400)

        if not name:
            return error("Customer name is required", 400)

        if not phone:
            return error("Customer phone is required", 400)

        if not address:
            return error("Customer address is required", 400)

        if not items:
            return error("Order items are required", 400)

        order_uid = (
            "WEB-"
            + datetime.now().strftime("%Y%m%d%H%M%S")
            + "-"
            + uuid.uuid4().hex[:6].upper()
        )

        created = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        conn = db()

        conn.execute(
            """
            INSERT INTO online_orders
            (
                order_uid,
                order_date,
                customer_name,
                customer_phone,
                address,
                items_summary,
                total_amount,
                payment_mode,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING')
            """,
            (
                order_uid,
                created,
                name,
                phone,
                address,
                items,
                total,
                mode,
            ),
        )

        conn.commit()
        conn.close()

        print(
            f"ONLINE ORDER SAVED: {order_uid} | "
            f"{name} | {phone} | ₹{total:.2f}"
        )

        return jsonify({
            "status": "success",
            "message": "Order saved successfully",
            "order_id": order_uid,
        }), 200

    except Exception as exc:
        print("SAVE ORDER ERROR:", repr(exc))
        return error("Unable to save order", 500)


@app.get("/api/orders")
def orders():
    """
    Protected endpoint used by the desktop POS.
    """
    if not authorized():
        return error("Unauthorized", 401)

    try:
        conn = db()

        rows = conn.execute(
            """
            SELECT
                id,
                order_uid,
                order_date,
                customer_name,
                customer_phone,
                address,
                items_summary,
                total_amount,
                payment_mode,
                status,
                delivered_at
            FROM online_orders
            ORDER BY id ASC
            """
        ).fetchall()

        conn.close()

        result = []

        for row in rows:
            result.append({
                "id": row["order_uid"],
                "created_at": row["order_date"],
                "customer_name": row["customer_name"],
                "phone": row["customer_phone"],
                "address": row["address"],
                "items": row["items_summary"],
                "total": row["total_amount"],
                "payment_mode": row["payment_mode"],
                "status": row["status"],
                "delivered_at": row["delivered_at"],
            })

        return jsonify({
            "status": "success",
            "orders": result,
        }), 200

    except Exception as exc:
        print("GET ORDERS ERROR:", repr(exc))
        return error("Unable to read orders", 500)


@app.post("/api/orders/<order_uid>/delivered")
def delivered(order_uid):
    """
    Protected endpoint used by the desktop POS.
    """
    if not authorized():
        return error("Unauthorized", 401)

    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        conn = db()

        cursor = conn.execute(
            """
            UPDATE online_orders
            SET
                status = 'DELIVERED',
                delivered_at = ?
            WHERE order_uid = ?
            """,
            (now, order_uid),
        )

        conn.commit()
        conn.close()

        if cursor.rowcount == 0:
            return error("Order not found", 404)

        return jsonify({
            "status": "success",
            "message": "Order marked as delivered",
            "order_id": order_uid,
        }), 200

    except Exception as exc:
        print("DELIVER ORDER ERROR:", repr(exc))
        return error("Unable to update order", 500)


init_db()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
