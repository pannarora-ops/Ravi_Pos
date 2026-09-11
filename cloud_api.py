import os
import sqlite3
import uuid
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

DB_PATH = os.environ.get("DB_PATH", "online_orders.db")
API_KEY = os.environ.get("API_KEY", "CHANGE_THIS_TO_YOUR_SECRET_KEY")

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
    return request.headers.get("X-API-Key", "") == API_KEY

@app.get("/")
def home():
    return jsonify({"status": "success", "service": "Ravi Confectionery Online Orders API"})

@app.post("/api/save_order")
def save_order():
    if not authorized():
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
    try:
        data = request.get_json(silent=True) or {}
        name = str(data.get("name", "")).strip()
        phone = str(data.get("phone", "")).strip()
        address = str(data.get("address", "")).strip()
        items = str(data.get("items", "")).strip()
        mode = str(data.get("mode", "Online")).strip() or "Online"
        total = float(data.get("total", 0) or 0)
        if not name or not phone or not address or not items:
            return jsonify({"status":"error","message":"Name, phone, address and items are required"}), 400
        uid = "WEB-" + datetime.now().strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6].upper()
        created = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = db()
        conn.execute("""INSERT INTO online_orders
            (order_uid, order_date, customer_name, customer_phone, address, items_summary, total_amount, payment_mode, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING')""",
            (uid, created, name, phone, address, items, total, mode))
        conn.commit()
        conn.close()
        return jsonify({"status":"success","order_id":uid}), 200
    except Exception as e:
        return jsonify({"status":"error","message":str(e)}), 500

@app.get("/api/orders")
def orders():
    if not authorized():
        return jsonify({"status":"error","message":"Unauthorized"}), 401
    try:
        conn = db()
        rows = conn.execute("SELECT id, order_uid, order_date, customer_name, customer_phone, address, items_summary, total_amount, payment_mode, status, delivered_at FROM online_orders ORDER BY id ASC").fetchall()
        conn.close()
        result = []
        for r in rows:
            result.append({
                "id": r["order_uid"], "created_at": r["order_date"],
                "customer_name": r["customer_name"], "phone": r["customer_phone"],
                "address": r["address"], "items": r["items_summary"],
                "total": r["total_amount"], "payment_mode": r["payment_mode"],
                "status": r["status"], "delivered_at": r["delivered_at"]
            })
        return jsonify({"status":"success","orders":result}), 200
    except Exception as e:
        return jsonify({"status":"error","message":str(e)}), 500

@app.post("/api/orders/<order_uid>/delivered")
def delivered(order_uid):
    if not authorized():
        return jsonify({"status":"error","message":"Unauthorized"}), 401
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = db()
        cur = conn.execute("UPDATE online_orders SET status='DELIVERED', delivered_at=? WHERE order_uid=?", (now, order_uid))
        conn.commit()
        conn.close()
        if cur.rowcount == 0:
            return jsonify({"status":"error","message":"Order not found"}), 404
        return jsonify({"status":"success","message":"Order marked as delivered"}), 200
    except Exception as e:
        return jsonify({"status":"error","message":str(e)}), 500

init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
