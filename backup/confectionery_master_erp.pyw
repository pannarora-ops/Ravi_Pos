import sys
import sqlite3
import random
import string
import urllib.parse
from datetime import datetime
from io import BytesIO
import qrcode
import threading
import json
import urllib.request
import urllib.error
from PyQt6.QtCore import QTimer
from flask import Flask, request, jsonify
from flask_cors import CORS

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QTableWidget, QTableWidgetItem, QLabel, QDialog, QLineEdit, 
    QHeaderView, QMessageBox, QAbstractItemView, QTabWidget, 
    QPushButton, QComboBox, QDateEdit, QCheckBox, QGroupBox,
    QRadioButton, QButtonGroup, QStackedWidget, QGridLayout, QFileDialog, QScrollArea
)
from PyQt6.QtCore import Qt, QDate, QUrl
from PyQt6.QtGui import QFont, QPixmap, QImage, QTextDocument, QDesktopServices
from PyQt6.QtPrintSupport import QPrinter, QPrintDialog

DEFAULT_UPI_ID = "9876543210@upi"
import os

CLOUD_API_KEY = os.environ.get("RAVI_POS_API_KEY", "").strip()

# ====================================================================
# 0. Flask API Server Core (Netlify to Local DB Sync)
# ====================================================================
server_app = Flask(__name__)
CORS(server_app)

api_db_instance = None

@server_app.route('/api/save_order', methods=['POST'])
def save_order_api():
    try:
        data = request.json
        name = data.get('name')
        phone = data.get('phone')
        addr = data.get('address')
        items = data.get('items')
        total = float(data.get('total', 0))
        mode = data.get('mode', 'Online')

        if api_db_instance:
            order_id = api_db_instance.add_online_order(name, phone, addr, items, total, mode)
            return jsonify({"status": "success", "order_id": order_id}), 200
        else:
            return jsonify({"status": "error", "message": "Database not initialized"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

def run_flask_server():
    server_app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)


# ====================================================================
# 1. डेटाबेस इंजन (Order Status Support & Dashboard Sync)
# ====================================================================
class ConfectioneryDB:
    def __init__(self, db_name="dukan_master.db"):
        self.conn = sqlite3.connect(db_name, check_same_thread=False)
        self.create_tables()

    def create_tables(self):
        cur = self.conn.cursor()
        
        cur.execute('''
            CREATE TABLE IF NOT EXISTS business_config (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shop_name TEXT,
                shop_address TEXT,
                phone1 TEXT,
                phone2 TEXT,
                whatsapp TEXT,
                gst_status TEXT, 
                shop_gstin TEXT,
                upi_id TEXT,
                logo_path TEXT,
                loyalty_earn_pct REAL DEFAULT 1.0,
                loyalty_point_val REAL DEFAULT 1.0
            )
        ''')

        cur.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                role TEXT DEFAULT 'Admin'
            )
        ''')

        cur.execute('''
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                sub_category TEXT DEFAULT ''
            )
        ''')

        cur.execute('''
            CREATE TABLE IF NOT EXISTS units_master (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                primary_unit TEXT NOT NULL,
                secondary_unit TEXT NOT NULL,
                conversion_factor REAL DEFAULT 1.0
            )
        ''')

        cur.execute('''
            CREATE TABLE IF NOT EXISTS customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                party_type TEXT DEFAULT 'GST',
                points REAL DEFAULT 0.0,
                due_balance REAL DEFAULT 0.0,
                gstin TEXT DEFAULT '',
                address TEXT DEFAULT ''
            )
        ''')

        cur.execute('''
            CREATE TABLE IF NOT EXISTS suppliers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                phone TEXT,
                party_type TEXT DEFAULT 'GST',
                gstin TEXT DEFAULT '',
                address TEXT DEFAULT '',
                pending_balance REAL DEFAULT 0.0
            )
        ''')

        cur.execute('''
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                barcode TEXT UNIQUE,
                name TEXT NOT NULL,
                category TEXT DEFAULT 'General',
                sub_category TEXT DEFAULT '',
                hsn_code TEXT DEFAULT '',
                primary_unit TEXT DEFAULT 'BOX',
                secondary_unit TEXT DEFAULT 'PCS',
                conversion_factor REAL DEFAULT 1.0,
                buy_rate REAL NOT NULL,
                sale_rate REAL NOT NULL,
                stock REAL NOT NULL,
                gst_rate REAL DEFAULT 5.0
            )
        ''')

        cur.execute('''
            CREATE TABLE IF NOT EXISTS coupons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE NOT NULL,
                customer_phone TEXT,
                discount_amount REAL NOT NULL,
                is_used INTEGER DEFAULT 0,
                created_date TEXT
            )
        ''')

        cur.execute('''
            CREATE TABLE IF NOT EXISTS online_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_date TEXT,
                customer_name TEXT,
                customer_phone TEXT,
                address TEXT,
                items_summary TEXT,
                total_amount REAL,
                payment_mode TEXT,
                status TEXT DEFAULT 'PENDING'
            )
        ''')

        cur.execute('''
            CREATE TABLE IF NOT EXISTS sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                customer_phone TEXT,
                payment_mode TEXT,
                subtotal REAL,
                cgst REAL DEFAULT 0.0,
                sgst REAL DEFAULT 0.0,
                points_redeemed REAL DEFAULT 0.0,
                total_amount REAL,
                total_profit REAL,
                points_earned REAL DEFAULT 0.0
            )
        ''')

        cur.execute('''
            CREATE TABLE IF NOT EXISTS sale_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sale_id INTEGER,
                product_id INTEGER,
                qty REAL,
                unit_sold TEXT,
                sale_rate REAL,
                buy_rate REAL,
                gst_rate REAL,
                line_total REAL
            )
        ''')

        cur.execute('''
            CREATE TABLE IF NOT EXISTS cashbook (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                entry_type TEXT, 
                category TEXT,
                amount REAL,
                remark TEXT
            )
        ''')
        self.conn.commit()
        self.ensure_online_order_sync_columns()

    def is_configured(self):
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM business_config")
        return cur.fetchone()[0] > 0

    def get_business_config(self):
        cur = self.conn.cursor()
        cur.execute("SELECT shop_name, shop_address, phone1, phone2, whatsapp, gst_status, shop_gstin, upi_id, logo_path, loyalty_earn_pct, loyalty_point_val FROM business_config LIMIT 1")
        row = cur.fetchone()
        if not row:
            return ("CONFECTIONERY BUSINESS", "", "", "", "", "REGISTERED", "", DEFAULT_UPI_ID, "", 1.0, 1.0)
        return row

    def save_business_config(self, name, addr, p1, p2, wa, gst_status, gstin, upi, logo, admin_user, admin_pass, earn_pct=1.0, pt_val=1.0):
        cur = self.conn.cursor()
        cur.execute("DELETE FROM business_config")
        cur.execute("INSERT INTO business_config (shop_name, shop_address, phone1, phone2, whatsapp, gst_status, shop_gstin, upi_id, logo_path, loyalty_earn_pct, loyalty_point_val) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (name, addr, p1, p2, wa, gst_status, gstin, upi, logo, earn_pct, pt_val))
        
        cur.execute("DELETE FROM users")
        cur.execute("INSERT INTO users (username, password, role) VALUES (?, ?, 'Admin')", (admin_user, admin_pass))
        
        cur.execute("SELECT COUNT(*) FROM categories")
        if cur.fetchone()[0] == 0:
            cats = [("Chocolates & Candies", "Bars"), ("Snacks & Biscuits", "Cookies"), ("Dairy & Butter", "Amul"), ("Cakes & Pastries", "Birthday Cakes")]
            cur.executemany("INSERT INTO categories (name, sub_category) VALUES (?, ?)", cats)

        cur.execute("SELECT COUNT(*) FROM units_master")
        if cur.fetchone()[0] == 0:
            units = [("BOX", "PCS", 24.0), ("CARTON", "PACKET", 12.0)]
            cur.executemany("INSERT INTO units_master (primary_unit, secondary_unit, conversion_factor) VALUES (?, ?, ?)", units)

        cur.execute("SELECT COUNT(*) FROM products")
        if cur.fetchone()[0] == 0:
            sample_items = [
                ("8901030001", "Cadbury Dairy Milk 50g", "Chocolates & Candies", "Bars", "1806", "BOX", "PCS", 24.0, 38.00, 45.00, 120.0, 18.0),
                ("8901725111", "Britannia Good Day", "Snacks & Biscuits", "Cookies", "1905", "CARTON", "PACKET", 12.0, 16.50, 20.00, 48.0, 12.0),
                ("101", "Fresh Cream Cake (Vanilla)", "Cakes & Pastries", "Birthday Cakes", "1905", "BOX", "PCS", 1.0, 220.00, 350.00, 10.0, 5.0)
            ]
            cur.executemany("INSERT INTO products (barcode, name, category, sub_category, hsn_code, primary_unit, secondary_unit, conversion_factor, buy_rate, sale_rate, stock, gst_rate) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", sample_items)
            
        self.conn.commit()

    def update_business_details(self, name, addr, p1, p2, wa, gst_status, gstin, upi):
        cur = self.conn.cursor()
        cur.execute('''
            UPDATE business_config 
            SET shop_name = ?, shop_address = ?, phone1 = ?, phone2 = ?, whatsapp = ?, gst_status = ?, shop_gstin = ?, upi_id = ?
        ''', (name, addr, p1, p2, wa, gst_status, gstin, upi))
        self.conn.commit()

    def update_loyalty_settings(self, earn_pct, pt_val):
        cur = self.conn.cursor()
        cur.execute("UPDATE business_config SET loyalty_earn_pct = ?, loyalty_point_val = ?", (earn_pct, pt_val))
        self.conn.commit()

    def verify_login(self, username, password):
        cur = self.conn.cursor()
        cur.execute("SELECT id, username, role FROM users WHERE username = ? AND password = ?", (username, password))
        return cur.fetchone()

    def get_all_categories(self):
        cur = self.conn.cursor()
        cur.execute("SELECT id, name, sub_category FROM categories ORDER BY name ASC")
        return cur.fetchall()

    def add_category(self, name, sub_cat):
        cur = self.conn.cursor()
        cur.execute("INSERT INTO categories (name, sub_category) VALUES (?, ?)", (name, sub_cat))
        self.conn.commit()

    def get_all_units_master(self):
        cur = self.conn.cursor()
        cur.execute("SELECT id, primary_unit, secondary_unit, conversion_factor FROM units_master ORDER BY primary_unit ASC")
        return cur.fetchall()

    def add_unit_master(self, p_unit, s_unit, conv):
        cur = self.conn.cursor()
        cur.execute("INSERT INTO units_master (primary_unit, secondary_unit, conversion_factor) VALUES (?, ?, ?)", (p_unit, s_unit, conv))
        self.conn.commit()

    def add_product(self, barcode, name, category, sub_cat, hsn, p_unit, s_unit, conv, buy_rate, sale_rate, stock, gst_rate):
        cur = self.conn.cursor()
        cur.execute('''
            INSERT INTO products (barcode, name, category, sub_category, hsn_code, primary_unit, secondary_unit, conversion_factor, buy_rate, sale_rate, stock, gst_rate)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (barcode, name, category, sub_cat, hsn, p_unit, s_unit, conv, buy_rate, sale_rate, stock, gst_rate))
        self.conn.commit()

    def update_product(self, prod_id, barcode, name, category, sub_cat, hsn, p_unit, s_unit, conv, buy_rate, sale_rate, stock, gst_rate):
        cur = self.conn.cursor()
        cur.execute('''
            UPDATE products 
            SET barcode = ?, name = ?, category = ?, sub_category = ?, hsn_code = ?, primary_unit = ?, secondary_unit = ?, conversion_factor = ?, buy_rate = ?, sale_rate = ?, stock = ?, gst_rate = ?
            WHERE id = ?
        ''', (barcode, name, category, sub_cat, hsn, p_unit, s_unit, conv, buy_rate, sale_rate, stock, gst_rate, prod_id))
        self.conn.commit()

    def get_product_by_id(self, prod_id):
        cur = self.conn.cursor()
        cur.execute("SELECT id, barcode, name, category, sub_category, hsn_code, primary_unit, secondary_unit, conversion_factor, buy_rate, sale_rate, stock, gst_rate FROM products WHERE id = ?", (prod_id,))
        return cur.fetchone()

    def get_product_by_barcode(self, barcode):
        cur = self.conn.cursor()
        cur.execute("SELECT id, barcode, name, category, sub_category, hsn_code, primary_unit, secondary_unit, conversion_factor, buy_rate, sale_rate, stock, gst_rate FROM products WHERE barcode = ?", (barcode,))
        return cur.fetchone()

    def search_products_by_name(self, text):
        cur = self.conn.cursor()
        cur.execute("SELECT id, barcode, name, category, sub_category, hsn_code, primary_unit, secondary_unit, conversion_factor, buy_rate, sale_rate, stock, gst_rate FROM products WHERE name LIKE ? ORDER BY name ASC", (f"%{text}%",))
        return cur.fetchall()

    def get_all_products(self):
        cur = self.conn.cursor()
        cur.execute("SELECT id, barcode, name, category, sub_category, hsn_code, primary_unit, secondary_unit, conversion_factor, buy_rate, sale_rate, stock, gst_rate FROM products ORDER BY id DESC")
        return cur.fetchall()

    def add_customer(self, phone, name, party_type, opening_due=0.0, gstin="", address=""):
        cur = self.conn.cursor()
        cur.execute('''
            INSERT INTO customers (phone, name, party_type, points, due_balance, gstin, address) 
            VALUES (?, ?, ?, 0.0, ?, ?, ?)
        ''', (phone, name, party_type, opening_due, gstin, address))
        self.conn.commit()

    def get_customer_by_phone(self, phone):
        cur = self.conn.cursor()
        cur.execute("SELECT id, name, party_type, points, due_balance, gstin, address FROM customers WHERE phone = ?", (phone,))
        return cur.fetchone()

    def get_all_customers(self):
        cur = self.conn.cursor()
        cur.execute("SELECT id, phone, name, party_type, points, due_balance, gstin, address FROM customers ORDER BY name ASC")
        return cur.fetchall()

    def add_supplier(self, name, phone, party_type, gstin, address, opening_balance):
        cur = self.conn.cursor()
        cur.execute('''
            INSERT INTO suppliers (name, phone, party_type, gstin, address, pending_balance) 
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (name, phone, party_type, gstin, address, opening_balance))
        self.conn.commit()

    def get_all_suppliers(self):
        cur = self.conn.cursor()
        cur.execute("SELECT id, name, phone, party_type, gstin, address, pending_balance FROM suppliers ORDER BY name ASC")
        return cur.fetchall()

    def add_cashbook_entry(self, entry_type, category, amount, remark):
        cur = self.conn.cursor()
        today = datetime.now().strftime("%Y-%m-%d")
        cur.execute("INSERT INTO cashbook (date, entry_type, category, amount, remark) VALUES (?, ?, ?, ?, ?)", 
                    (today, entry_type, category, amount, remark))
        self.conn.commit()

    def generate_coupon(self, phone, amount):
        cur = self.conn.cursor()
        code = "COUPON-" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        today = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute("INSERT INTO coupons (code, customer_phone, discount_amount, is_used, created_date) VALUES (?, ?, ?, 0, ?)",
                    (code, phone, amount, today))
        self.conn.commit()
        return code

    def verify_and_use_coupon(self, code):
        cur = self.conn.cursor()
        cur.execute("SELECT id, discount_amount, is_used FROM coupons WHERE code = ?", (code,))
        row = cur.fetchone()
        if not row:
            return 0.0, "अमान्य कूपन कोड!"
        if row[2] == 1:
            return 0.0, "यह कूपन पहले ही इस्तेमाल किया जा चुका है!"
        
        cur.execute("UPDATE coupons SET is_used = 1 WHERE id = ?", (row[0],))
        self.conn.commit()
        return row[1], f"सफल! ₹{row[1]:.2f} की छूट लागू हो गई।"

    def add_online_order(self, name, phone, addr, items, total, mode):
        cur = self.conn.cursor()
        today = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute('''
            INSERT INTO online_orders (order_date, customer_name, customer_phone, address, items_summary, total_amount, payment_mode, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING')
        ''', (today, name, phone, addr, items, total, mode))
        self.conn.commit()
        return cur.lastrowid

    def ensure_online_order_sync_columns(self):
        """Add cloud-sync columns to older databases without losing existing orders."""
        cur = self.conn.cursor()
        cur.execute("PRAGMA table_info(online_orders)")
        cols = {row[1] for row in cur.fetchall()}
        if "cloud_order_id" not in cols:
            cur.execute("ALTER TABLE online_orders ADD COLUMN cloud_order_id TEXT")
        if "cloud_synced" not in cols:
            cur.execute("ALTER TABLE online_orders ADD COLUMN cloud_synced INTEGER DEFAULT 0")
        self.conn.commit()

    def save_cloud_order_local(self, order):
        """Insert a cloud order locally once; returns True when a new order was added."""
        self.ensure_online_order_sync_columns()
        cloud_id = str(order.get("id", "")).strip()
        if not cloud_id:
            return False
        cur = self.conn.cursor()
        cur.execute("SELECT id FROM online_orders WHERE cloud_order_id = ?", (cloud_id,))
        if cur.fetchone():
            return False
        cur.execute("""
            INSERT INTO online_orders
            (order_date, customer_name, customer_phone, address, items_summary,
             total_amount, payment_mode, status, cloud_order_id, cloud_synced)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """, (
            order.get("created_at", ""),
            order.get("customer_name", ""),
            order.get("phone", ""),
            order.get("address", ""),
            order.get("items", ""),
            float(order.get("total", 0) or 0),
            order.get("payment_mode", "Online"),
            order.get("status", "PENDING"),
            cloud_id
        ))
        self.conn.commit()
        return True

    def get_cloud_id_for_local_order(self, local_id):
        self.ensure_online_order_sync_columns()
        cur = self.conn.cursor()
        cur.execute("SELECT cloud_order_id FROM online_orders WHERE id = ?", (local_id,))
        row = cur.fetchone()
        return row[0] if row and row[0] else None

    def update_order_status_db(self, order_id, new_status):
        cur = self.conn.cursor()
        cur.execute("UPDATE online_orders SET status = ? WHERE id = ?", (new_status, order_id))
        self.conn.commit()

    def process_sale_with_gst(self, cust_phone, cust_name, pay_mode, items, redeem_points=0.0, earn_pct=1.0):
        cur = self.conn.cursor()
        today = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        subtotal = 0.0
        total_cgst = 0.0
        total_sgst = 0.0

        processed_items = []
        for prod_id, qty, unit_sold, s_rate, buy_rate, gst_rate, conv_factor in items:
            actual_stock_deduct = qty / conv_factor if unit_sold != "PRIMARY" else qty
            line_amt = qty * s_rate
            subtotal += line_amt
            item_tax = line_amt * (gst_rate / 100.0)
            total_cgst += item_tax / 2.0
            total_sgst += item_tax / 2.0
            processed_items.append((prod_id, qty, unit_sold, s_rate, buy_rate, gst_rate, line_amt + item_tax, actual_stock_deduct))

        actual_redeem = min(redeem_points, subtotal + total_cgst + total_sgst)
        total_taxable_bill = (subtotal + total_cgst + total_sgst) - actual_redeem
        
        earned_points = round(total_taxable_bill * (earn_pct / 100.0), 2) if (cust_phone and cust_phone != "WALK-IN") else 0.0
        total_profit = sum(qty * (s_rate - buy_rate) for _, qty, _, s_rate, buy_rate, _, _, _ in processed_items) - actual_redeem

        cur.execute('''
            INSERT INTO sales (date, customer_phone, payment_mode, subtotal, cgst, sgst, points_redeemed, total_amount, total_profit, points_earned)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (today, cust_phone, pay_mode, subtotal, total_cgst, total_sgst, actual_redeem, total_taxable_bill, total_profit, earned_points))
        sale_id = cur.lastrowid

        for prod_id, qty, unit_sold, s_rate, b_rate, gst_rate, l_tot, stock_deduct in processed_items:
            cur.execute('''
                INSERT INTO sale_items (sale_id, product_id, qty, unit_sold, sale_rate, buy_rate, gst_rate, line_total)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (sale_id, prod_id, qty, unit_sold, s_rate, b_rate, gst_rate, l_tot))
            cur.execute("UPDATE products SET stock = stock - ? WHERE id = ?", (stock_deduct, prod_id))

        if cust_phone and cust_phone != "WALK-IN":
            due_add = total_taxable_bill if pay_mode == "UDHARI (उधार)" else 0.0
            cur.execute('''
                UPDATE customers 
                SET points = points - ? + ?, due_balance = due_balance + ?
                WHERE phone = ?
            ''', (actual_redeem, earned_points, due_add, cust_phone))

        if pay_mode in ["CASH", "UPI / QR"]:
            cur.execute("INSERT INTO cashbook (date, entry_type, category, amount, remark) VALUES (?, 'INCOME', 'Sales Collection', ?, ?)", 
                        (today[:10], total_taxable_bill, f"Bill #{sale_id} Collection"))

        self.conn.commit()
        return sale_id, today, subtotal, total_cgst, total_sgst, actual_redeem, total_taxable_bill, earned_points

    def get_dashboard_stats(self):
        cur = self.conn.cursor()
        today_str = datetime.now().strftime("%Y-%m-%d")
        cur.execute("SELECT COALESCE(SUM(total_amount), 0.0) FROM sales WHERE date LIKE ?", (f"{today_str}%",))
        today_sales = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*), COALESCE(SUM(CASE WHEN stock <= 5 THEN 1 ELSE 0 END), 0) FROM products")
        total_prod, low_stock = cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM online_orders WHERE status = 'PENDING'")
        pending_orders = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM online_orders WHERE status = 'DELIVERED'")
        delivered_orders = cur.fetchone()[0]
        return today_sales, total_prod, low_stock, pending_orders, delivered_orders


# ====================================================================
# 2. सेटअप विज़ार्ड
# ====================================================================
class SetupWizardDialog(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.logo_path = ""
        self.setWindowTitle("Business Registration & Setup Wizard")
        self.setFixedSize(780, 640)
        self.setup_ui()

    def setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(15, 15, 15, 15)

        lbl_title = QLabel("🏪 WELCOME TO ERP SETUP", alignment=Qt.AlignmentFlag.AlignCenter)
        lbl_title.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        lbl_title.setStyleSheet("color: #005fb8; margin-bottom: 5px;")
        main_layout.addWidget(lbl_title)

        lbl_sub = QLabel("अपने व्यवसाय की जानकारी, लोगो और एडमिन क्रेडेंशियल्स दर्ज करें:")
        lbl_sub.setStyleSheet("font-weight: bold; margin-bottom: 10px; color: #555;")
        main_layout.addWidget(lbl_sub)

        grid = QGridLayout()
        grid.setSpacing(12)

        self.txt_name = QLineEdit()
        self.txt_name.setPlaceholderText("Business Name (उदा. Ravi Confectionery)")
        self.txt_addr = QLineEdit()
        self.txt_addr.setPlaceholderText("Business Address")
        
        self.txt_phone1 = QLineEdit()
        self.txt_phone1.setPlaceholderText("Primary Mobile Number")
        self.txt_phone2 = QLineEdit()
        self.txt_phone2.setPlaceholderText("Secondary Mobile Number (Optional)")
        self.txt_whatsapp = QLineEdit()
        self.txt_whatsapp.setPlaceholderText("WhatsApp Number")

        self.cmb_gst_status = QComboBox()
        self.cmb_gst_status.addItems(["GST Registered", "GST Non-Registered (Unregistered)"])
        self.cmb_gst_status.currentIndexChanged.connect(self.toggle_gstin_field)

        self.txt_gstin = QLineEdit()
        self.txt_gstin.setPlaceholderText("15-Digit GSTIN")

        self.txt_upi = QLineEdit()
        self.txt_upi.setPlaceholderText("UPI ID (9876543210@upi)")

        self.txt_admin_user = QLineEdit("admin")
        self.txt_admin_pass = QLineEdit("admin123")
        self.txt_admin_pass.setEchoMode(QLineEdit.EchoMode.Password)

        self.btn_logo = QPushButton("📁 Upload Business Logo")
        self.btn_logo.setStyleSheet("background-color: #6c757d; color: white; font-weight: bold; padding: 6px; border-radius: 4px;")
        self.btn_logo.clicked.connect(self.browse_logo)
        self.lbl_logo_status = QLabel("No logo selected")

        fields = [
            ("Business Name:", self.txt_name, 0, 0),
            ("Address:", self.txt_addr, 1, 0),
            ("Primary Mobile:", self.txt_phone1, 2, 0),
            ("Secondary Mobile:", self.txt_phone2, 3, 0),
            ("WhatsApp Number:", self.txt_whatsapp, 4, 0),
            ("GST Status:", self.cmb_gst_status, 0, 1),
            ("GSTIN Number:", self.txt_gstin, 1, 1),
            ("UPI ID for QR:", self.txt_upi, 2, 1),
            ("Admin Username:", self.txt_admin_user, 3, 1),
            ("Admin Password:", self.txt_admin_pass, 4, 1)
        ]

        for label_text, widget, r, c in fields:
            v_box = QVBoxLayout()
            lbl = QLabel(label_text)
            lbl.setStyleSheet("font-weight: 600; color: #333;")
            widget.setStyleSheet("padding: 7px; font-size: 13px; border: 1px solid #ccc; border-radius: 4px;")
            v_box.addWidget(lbl)
            v_box.addWidget(widget)
            grid.addLayout(v_box, r, c)

        main_layout.addLayout(grid)

        logo_layout = QHBoxLayout()
        logo_layout.addWidget(self.btn_logo)
        logo_layout.addWidget(self.lbl_logo_status)
        main_layout.addLayout(logo_layout)

        main_layout.addSpacing(10)
        btn_save = QPushButton("Save & Launch Software")
        btn_save.setStyleSheet("background-color: #107c41; color: white; font-weight: bold; height: 45px; border-radius: 6px; font-size: 14px;")
        btn_save.clicked.connect(self.save_setup)
        main_layout.addWidget(btn_save)

        self.toggle_gstin_field()

    def toggle_gstin_field(self):
        is_registered = "Registered" in self.cmb_gst_status.currentText() and "Non" not in self.cmb_gst_status.currentText()
        if is_registered:
            self.txt_gstin.setEnabled(True)
            self.txt_gstin.setStyleSheet("padding: 7px; font-size: 13px; border: 1px solid #ccc; border-radius: 4px; background: #fff;")
        else:
            self.txt_gstin.clear()
            self.txt_gstin.setEnabled(False)
            self.txt_gstin.setStyleSheet("padding: 7px; font-size: 13px; border: 1px solid #ccc; border-radius: 4px; background: #e9ecef;")

    def browse_logo(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Business Logo", "", "Image Files (*.png *.jpg *.jpeg)")
        if path:
            self.logo_path = path
            self.lbl_logo_status.setText(path.split("/")[-1])

    def save_setup(self):
        name = self.txt_name.text().strip()
        user = self.txt_admin_user.text().strip()
        pwd = self.txt_admin_pass.text().strip()
        if not name or not user or not pwd:
            QMessageBox.warning(self, "त्रुटि", "व्यवसाय का नाम, यूजरनेम और पासवर्ड अनिवार्य है!")
            return

        gst_stat = "REGISTERED" if "Registered" in self.cmb_gst_status.currentText() and "Non" not in self.cmb_gst_status.currentText() else "UNREGISTERED"
        
        self.db.save_business_config(
            name, self.txt_addr.text().strip(), self.txt_phone1.text().strip(),
            self.txt_phone2.text().strip(), self.txt_whatsapp.text().strip(),
            gst_stat, self.txt_gstin.text().strip(), self.txt_upi.text().strip(),
            self.logo_path, user, pwd, 1.0, 1.0
        )
        QMessageBox.information(self, "सफल", "व्यवसाय रजिस्ट्रेशन सफल रहा!")
        self.accept()


# ====================================================================
# 3. मैनुअल ऑर्डर जोड़ने के लिए डायलॉग
# ====================================================================
class AddManualOrderDialog(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("Add Online Order Manually")
        self.setFixedSize(450, 400)
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<b>➕ दर्ज करें ऑनलाइन प्राप्त ऑर्डर (Manual Entry)</b>", alignment=Qt.AlignmentFlag.AlignCenter))

        self.txt_name = QLineEdit()
        self.txt_name.setPlaceholderText("ग्राहक का नाम (Customer Name)")
        self.txt_phone = QLineEdit()
        self.txt_phone.setPlaceholderText("मोबाइल नंबर (Mobile Number)")
        self.txt_addr = QLineEdit()
        self.txt_addr.setPlaceholderText("डिलीवरी पता (Address)")
        self.txt_items = QLineEdit()
        self.txt_items.setPlaceholderText("आइटम विवरण व मात्रा (उदा. Cadbury x 2)")
        self.txt_total = QLineEdit()
        self.txt_total.setPlaceholderText("कुल राशि (Total Amount ₹)")

        layout.addWidget(QLabel("Customer Name:"))
        layout.addWidget(self.txt_name)
        layout.addWidget(QLabel("Mobile Number:"))
        layout.addWidget(self.txt_phone)
        layout.addWidget(QLabel("Delivery Address:"))
        layout.addWidget(self.txt_addr)
        layout.addWidget(QLabel("Items & Qty Summary:"))
        layout.addWidget(self.txt_items)
        layout.addWidget(QLabel("Total Amount (₹):"))
        layout.addWidget(self.txt_total)

        btn_save = QPushButton("Save Online Order")
        btn_save.setStyleSheet("background-color: #107c41; color: white; font-weight: bold; height: 38px; border-radius: 6px;")
        btn_save.clicked.connect(self.save_order)
        layout.addWidget(btn_save)

    def save_order(self):
        name = self.txt_name.text().strip()
        phone = self.txt_phone.text().strip()
        items = self.txt_items.text().strip()
        try:
            total = float(self.txt_total.text().strip())
        except ValueError:
            total = 0.0

        if not name or not phone or not items:
            QMessageBox.warning(self, "त्रुटि", "नाम, फोन और आइटम विवरण अनिवार्य है!")
            return

        self.db.add_online_order(name, phone, self.txt_addr.text().strip(), items, total, "Online")
        QMessageBox.information(self, "सफल", "ऑनलाइन आर्डर सफलतापूर्वक दर्ज हो गया!")
        self.accept()


# ====================================================================
# 4. अन्य पॉपअप डायलॉग्स
# ====================================================================
class EditProductDialog(QDialog):
    def __init__(self, db, prod_id, parent=None):
        super().__init__(parent)
        self.db = db
        self.prod_id = prod_id
        self.setWindowTitle("Edit Product, HSN & Units")
        self.setFixedSize(500, 580)
        self.setup_ui()
        self.load_current_data()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 15, 20, 20)
        layout.setSpacing(10)
        layout.addWidget(QLabel("<b>EDIT PRODUCT DETAILS</b>", alignment=Qt.AlignmentFlag.AlignCenter))

        row1 = QHBoxLayout()
        self.txt_barcode = QLineEdit()
        self.txt_name = QLineEdit()
        row1.addWidget(QLabel("Barcode:"))
        row1.addWidget(self.txt_barcode, 2)
        row1.addWidget(QLabel("Name:"))
        row1.addWidget(self.txt_name, 3)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        self.cmb_category = QComboBox()
        for c in self.db.get_all_categories(): self.cmb_category.addItem(c[1])
        self.txt_sub_cat = QLineEdit()
        self.txt_sub_cat.setPlaceholderText("Sub-Category")
        self.txt_hsn = QLineEdit()
        self.txt_hsn.setPlaceholderText("HSN")
        row2.addWidget(QLabel("Cat:"))
        row2.addWidget(self.cmb_category)
        row2.addWidget(QLabel("Sub-Cat:"))
        row2.addWidget(self.txt_sub_cat)
        row2.addWidget(QLabel("HSN:"))
        row2.addWidget(self.txt_hsn)
        layout.addLayout(row2)

        u_box = QGroupBox("Unit & Conversion Settings")
        u_layout = QGridLayout(u_box)
        self.txt_p_unit = QLineEdit()
        self.txt_s_unit = QLineEdit()
        self.txt_conv = QLineEdit("1.0")
        
        u_layout.addWidget(QLabel("Primary Unit:"), 0, 0)
        u_layout.addWidget(self.txt_p_unit, 0, 1)
        u_layout.addWidget(QLabel("Secondary Unit:"), 1, 0)
        u_layout.addWidget(self.txt_s_unit, 1, 1)
        u_layout.addWidget(QLabel("Conversion:"), 2, 0)
        u_layout.addWidget(self.txt_conv, 2, 1)
        layout.addWidget(u_box)

        row3 = QHBoxLayout()
        self.txt_buy_rate = QLineEdit()
        self.txt_sale_rate = QLineEdit()
        row3.addWidget(QLabel("Buy Rate:"))
        row3.addWidget(self.txt_buy_rate)
        row3.addWidget(QLabel("Sale Rate:"))
        row3.addWidget(self.txt_sale_rate)
        layout.addLayout(row3)

        row4 = QHBoxLayout()
        self.txt_stock = QLineEdit()
        self.cmb_gst = QComboBox()
        self.cmb_gst.addItems(["0.0", "5.0", "12.0", "18.0", "28.0"])
        row4.addWidget(QLabel("Stock:"))
        row4.addWidget(self.txt_stock)
        row4.addWidget(QLabel("GST %:"))
        row4.addWidget(self.cmb_gst)
        layout.addLayout(row4)

        btn_box = QHBoxLayout()
        btn_update = QPushButton("Update Product")
        btn_update.setStyleSheet("background-color: #0078d4; color: white; font-weight: bold; border-radius: 6px; height: 38px;")
        btn_update.clicked.connect(self.save_update)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.setStyleSheet("background-color: #e0e0e0; font-weight: bold; border-radius: 6px; height: 38px;")
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_update)
        btn_box.addWidget(btn_cancel)
        layout.addLayout(btn_box)

    def load_current_data(self):
        prod = self.db.get_product_by_id(self.prod_id)
        if prod:
            self.txt_barcode.setText(str(prod[1]))
            self.txt_name.setText(str(prod[2]))
            self.cmb_category.setCurrentText(str(prod[3]))
            self.txt_sub_cat.setText(str(prod[4]))
            self.txt_hsn.setText(str(prod[5]))
            self.txt_p_unit.setText(str(prod[6]))
            self.txt_s_unit.setText(str(prod[7]))
            self.txt_conv.setText(str(prod[8]))
            self.txt_buy_rate.setText(f"{prod[9]:.2f}")
            self.txt_sale_rate.setText(f"{prod[10]:.2f}")
            self.txt_stock.setText(f"{prod[11]:.2f}")
            self.cmb_gst.setCurrentText(str(prod[12]))

    def save_update(self):
        try:
            self.db.update_product(
                self.prod_id, self.txt_barcode.text().strip(), self.txt_name.text().strip(),
                self.cmb_category.currentText(), self.txt_sub_cat.text().strip(), self.txt_hsn.text().strip(),
                self.txt_p_unit.text().strip().upper(), self.txt_s_unit.text().strip().upper(),
                float(self.txt_conv.text() or 1.0), float(self.txt_buy_rate.text()),
                float(self.txt_sale_rate.text()), float(self.txt_stock.text()), float(self.cmb_gst.currentText())
            )
            QMessageBox.information(self, "सफल", "प्रोडक्ट अपडेट हो गया!")
            self.accept()
        except Exception as e:
            QMessageBox.warning(self, "त्रुटि", str(e))


class QuantityDialog(QDialog):
    def __init__(self, item_name, p_unit, s_unit, conv_factor, stock, rate, parent=None):
        super().__init__(parent)
        self.entered_qty = 1.0
        self.selected_unit = s_unit
        self.setWindowTitle(f"Enter Quantity - {item_name}")
        self.setFixedSize(400, 280)
        
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<b>{item_name}</b>", alignment=Qt.AlignmentFlag.AlignCenter))
        layout.addWidget(QLabel(f"Rate: ₹{rate:.2f} | Stock: {stock} {p_unit} (1 {p_unit}={conv_factor} {s_unit})", alignment=Qt.AlignmentFlag.AlignCenter))
        
        u_layout = QHBoxLayout()
        self.rb_sec = QRadioButton(f"Secondary Unit ({s_unit})")
        self.rb_pri = QRadioButton(f"Primary Unit ({p_unit})")
        self.rb_sec.setChecked(True)
        u_layout.addWidget(self.rb_sec)
        u_layout.addWidget(self.rb_pri)
        layout.addLayout(u_layout)

        layout.addWidget(QLabel("मात्रा दर्ज करें:"))
        self.txt_qty = QLineEdit("1")
        self.txt_qty.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        self.txt_qty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.txt_qty.selectAll()
        self.txt_qty.returnPressed.connect(self.confirm_qty)
        layout.addWidget(self.txt_qty)

        btn_box = QHBoxLayout()
        btn_ok = QPushButton("Add (Enter)")
        btn_ok.setStyleSheet("background-color: #107c41; color: white; font-weight: bold; border-radius: 6px; height: 35px;")
        btn_ok.clicked.connect(self.confirm_qty)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.setStyleSheet("background-color: #e0e0e0; font-weight: bold; border-radius: 6px; height: 35px;")
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_ok)
        btn_box.addWidget(btn_cancel)
        layout.addLayout(btn_box)
        self.txt_qty.setFocus()

    def confirm_qty(self):
        try:
            val = float(self.txt_qty.text().strip())
            if val <= 0: raise ValueError
            self.entered_qty = val
            self.selected_unit = self.rb_pri.text().split()[0] if self.rb_pri.isChecked() else "SEC"
            self.accept()
        except ValueError:
            QMessageBox.warning(self, "त्रुटि", "सही मात्रा भरें!")


class LoginDialog(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.user_data = None
        self.setWindowTitle("Login - POS")
        self.setFixedSize(380, 260)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<b>STORE LOGIN</b>", alignment=Qt.AlignmentFlag.AlignCenter))
        self.txt_username = QLineEdit()
        self.txt_username.setPlaceholderText("Username")
        self.txt_password = QLineEdit()
        self.txt_password.setPlaceholderText("Password")
        self.txt_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_password.returnPressed.connect(self.handle_login)
        layout.addWidget(QLabel("Username:"))
        layout.addWidget(self.txt_username)
        layout.addWidget(QLabel("Password:"))
        layout.addWidget(self.txt_password)
        btn = QPushButton("Login")
        btn.setStyleSheet("background-color: #0078d4; color: white; font-weight: bold; height: 38px; border-radius: 6px;")
        btn.clicked.connect(self.handle_login)
        layout.addWidget(btn)
        self.txt_username.setFocus()

    def handle_login(self):
        user = self.db.verify_login(self.txt_username.text().strip(), self.txt_password.text().strip())
        if user:
            self.user_data = user
            self.accept()
        else:
            QMessageBox.warning(self, "त्रुटि", "गलत यूजरनेम या पासवर्ड!")


class AddCustomerDialog(QDialog):
    def __init__(self, db, pre_phone="", parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("Add Customer (GST / Non-GST)")
        self.setFixedSize(400, 400)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<b>NEW CUSTOMER DETAILS</b>", alignment=Qt.AlignmentFlag.AlignCenter))
        
        self.txt_phone = QLineEdit(pre_phone)
        self.txt_name = QLineEdit()
        self.cmb_type = QComboBox()
        self.cmb_type.addItems(["GST Registered", "Non-GST (Unregistered)"])
        self.txt_gstin = QLineEdit()
        self.txt_gstin.setPlaceholderText("15-Digit GSTIN (Optional)")
        self.txt_address = QLineEdit()
        self.txt_address.setPlaceholderText("Address (Optional)")

        layout.addWidget(QLabel("Mobile No:"))
        layout.addWidget(self.txt_phone)
        layout.addWidget(QLabel("Full Name:"))
        layout.addWidget(self.txt_name)
        layout.addWidget(QLabel("Party Type:"))
        layout.addWidget(self.cmb_type)
        layout.addWidget(QLabel("GSTIN (यदि हो):"))
        layout.addWidget(self.txt_gstin)
        layout.addWidget(QLabel("Address (वैकल्पिक):"))
        layout.addWidget(self.txt_address)

        btn = QPushButton("Save Customer")
        btn.setStyleSheet("background-color: #107c41; color: white; font-weight: bold; height: 38px; border-radius: 6px;")
        btn.clicked.connect(self.save_cust)
        layout.addWidget(btn)

    def save_cust(self):
        if len(self.txt_phone.text().strip()) < 10 or not self.txt_name.text().strip():
            QMessageBox.warning(self, "त्रुटि", "मोबाइल और नाम अनिवार्य है!")
            return
        try:
            ptype = "GST" if "GST Registered" in self.cmb_type.currentText() else "NON-GST"
            self.db.add_customer(
                self.txt_phone.text().strip(), self.txt_name.text().strip(), ptype, 
                0.0, self.txt_gstin.text().strip(), self.txt_address.text().strip()
            )
            QMessageBox.information(self, "सफल", "ग्राहक जुड़ गया!")
            self.accept()
        except Exception as e:
            QMessageBox.warning(self, "त्रुटि", "यह मोबाइल नंबर पहले से पंजीकृत है!")


class AddSupplierDialog(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("Add Supplier (GST / Non-GST)")
        self.setFixedSize(400, 440)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<b>NEW SUPPLIER DETAILS</b>", alignment=Qt.AlignmentFlag.AlignCenter))
        
        self.txt_name = QLineEdit()
        self.txt_phone = QLineEdit()
        self.cmb_type = QComboBox()
        self.cmb_type.addItems(["GST Registered", "Non-GST (Unregistered)"])
        self.txt_gstin = QLineEdit()
        self.txt_gstin.setPlaceholderText("15-Digit GSTIN (Optional)")
        self.txt_address = QLineEdit()
        self.txt_address.setPlaceholderText("Address (Optional)")
        self.txt_bal = QLineEdit("0.0")

        layout.addWidget(QLabel("Agency / Supplier Name:"))
        layout.addWidget(self.txt_name)
        layout.addWidget(QLabel("Phone No:"))
        layout.addWidget(self.txt_phone)
        layout.addWidget(QLabel("Party Type:"))
        layout.addWidget(self.cmb_type)
        layout.addWidget(QLabel("GSTIN (यदि हो):"))
        layout.addWidget(self.txt_gstin)
        layout.addWidget(QLabel("Address (वैकल्पिक):"))
        layout.addWidget(self.txt_address)
        layout.addWidget(QLabel("Opening Balance Due (₹):"))
        layout.addWidget(self.txt_bal)

        btn = QPushButton("Save Supplier")
        btn.setStyleSheet("background-color: #d83b01; color: white; font-weight: bold; height: 38px; border-radius: 6px;")
        btn.clicked.connect(self.save_supp)
        layout.addWidget(btn)

    def save_supp(self):
        name = self.txt_name.text().strip()
        if not name: return
        try:
            ptype = "GST" if "GST Registered" in self.cmb_type.currentText() else "NON-GST"
            bal = float(self.txt_bal.text().strip() or 0.0)
            self.db.add_supplier(
                name, self.txt_phone.text().strip(), ptype, 
                self.txt_gstin.text().strip(), self.txt_address.text().strip(), bal
            )
            QMessageBox.information(self, "सफल", "सप्लायर जुड़ गया!")
            self.accept()
        except Exception as e:
            QMessageBox.warning(self, "त्रुटि", "सप्लायर पहले से मौजूद है!")


class PaymentSettlementDialog(QDialog):
    def __init__(self, payable_amount, shop_name, upi_id, cust_points=0.0, pt_val=1.0, parent=None):
        super().__init__(parent)
        self.payable_amount = payable_amount
        self.shop_name = shop_name
        self.upi_id = upi_id
        self.cust_points = cust_points
        self.pt_val = pt_val
        self.selected_mode = "CASH"
        self.cash_tendered = payable_amount
        self.redeemed_points = 0.0
        self.final_payable = payable_amount

        self.setFixedSize(480, 580)
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        self.lbl_net = QLabel(f"<b>NET PAYABLE: ₹ {self.final_payable:.2f}</b>", alignment=Qt.AlignmentFlag.AlignCenter)
        self.lbl_net.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        layout.addWidget(self.lbl_net)

        if self.cust_points > 0:
            p_box = QGroupBox(f"Loyalty Points Available: {self.cust_points:.1f} (1 Pt = ₹{self.pt_val})")
            p_layout = QHBoxLayout(p_box)
            self.chk_redeem = QCheckBox("Redeem All Points")
            self.chk_redeem.stateChanged.connect(self.toggle_redeem)
            p_layout.addWidget(self.chk_redeem)
            layout.addWidget(p_box)

        mode_box = QGroupBox("Payment Mode (कीबोर्ड से 1, 2, 3 दबाएं):")
        m_layout = QGridLayout(mode_box)
        self.rb_cash = QRadioButton("1. CASH")
        self.rb_upi = QRadioButton("2. UPI QR")
        self.rb_udhari = QRadioButton("3. UDHARI (उधार)")
        self.rb_cash.setChecked(True)

        self.btn_group = QButtonGroup(self)
        self.btn_group.addButton(self.rb_cash, 1)
        self.btn_group.addButton(self.rb_upi, 2)
        self.btn_group.addButton(self.rb_udhari, 3)
        self.btn_group.idClicked.connect(self.mode_changed)

        m_layout.addWidget(self.rb_cash, 0, 0)
        m_layout.addWidget(self.rb_upi, 0, 1)
        m_layout.addWidget(self.rb_udhari, 1, 0)
        layout.addWidget(mode_box)

        self.stack = QStackedWidget()
        p0 = QWidget()
        l0 = QVBoxLayout(p0)
        self.txt_tendered = QLineEdit(f"{self.final_payable:.2f}")
        self.lbl_change = QLabel("Change Due: ₹ 0.00")
        self.txt_tendered.textChanged.connect(self.calc_change)
        l0.addWidget(QLabel("Cash Received:"))
        l0.addWidget(self.txt_tendered)
        l0.addWidget(self.lbl_change)
        self.stack.addWidget(p0)

        p1 = QWidget()
        l1 = QVBoxLayout(p1)
        self.lbl_qr = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.generate_qr()
        l1.addWidget(self.lbl_qr)
        self.stack.addWidget(p1)

        p2 = QWidget()
        l2 = QVBoxLayout(p2)
        l2.addWidget(QLabel("यह बिल ग्राहक के उधार खाते में दर्ज हो जाएगा।", alignment=Qt.AlignmentFlag.AlignCenter))
        self.stack.addWidget(p2)

        layout.addWidget(self.stack)

        btn_ok = QPushButton("Confirm & Print Bill")
        btn_ok.setStyleSheet("background-color: #107c41; color: white; font-weight: bold; height: 42px; border-radius: 6px;")
        btn_ok.clicked.connect(self.accept)
        layout.addWidget(btn_ok)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_1:
            self.rb_cash.setChecked(True)
            self.mode_changed(1)
        elif event.key() == Qt.Key.Key_2:
            self.rb_upi.setChecked(True)
            self.mode_changed(2)
        elif event.key() == Qt.Key.Key_3:
            self.rb_udhari.setChecked(True)
            self.mode_changed(3)
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.accept()
        else:
            super().keyPressEvent(event)

    def toggle_redeem(self, state):
        if state == 2:
            self.redeemed_points = min(self.cust_points, self.payable_amount / self.pt_val)
            self.final_payable = self.payable_amount - (self.redeemed_points * self.pt_val)
        else:
            self.redeemed_points = 0.0
            self.final_payable = self.payable_amount
        
        self.lbl_net.setText(f"<b>NET PAYABLE: ₹ {self.final_payable:.2f}</b>")
        self.txt_tendered.setText(f"{self.final_payable:.2f}")
        self.calc_change()
        self.generate_qr()

    def mode_changed(self, bid):
        if bid == 1:
            self.selected_mode = "CASH"
            self.stack.setCurrentIndex(0)
        elif bid == 2:
            self.selected_mode = "UPI / QR"
            self.stack.setCurrentIndex(1)
        else:
            self.selected_mode = "UDHARI (उधार)"
            self.stack.setCurrentIndex(2)

    def calc_change(self):
        try:
            t = float(self.txt_tendered.text() or 0)
            self.cash_tendered = t
            diff = t - self.final_payable
            self.lbl_change.setText(f"Change Due: ₹ {max(0, diff):.2f}")
        except: pass

    def generate_qr(self):
        url = f"upi://pay?pa={self.upi_id}&pn={self.shop_name}&am={self.final_payable:.2f}&cu=INR"
        qr = qrcode.QRCode(box_size=4, border=1)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = BytesIO()
        img.save(buf, "PNG")
        pix = QPixmap.fromImage(QImage.fromData(buf.getvalue()))
        self.lbl_qr.setPixmap(pix)


# ====================================================================
# 5. मुख्य सॉफ्टवेयर (Settings Tab Business Edit & Split Pending/Delivered)
# ====================================================================
class ConfectioneryMasterERP(QMainWindow):
    def __init__(self, current_user=("admin", "Admin"), config=None):
        super().__init__()
        self.db = ConfectioneryDB()
        
        global api_db_instance
        api_db_instance = self.db

        self.current_user = current_user
        self.load_config()

        self.current_cust_name = "Walk-in Customer"
        self.current_cust_phone = "WALK-IN"
        self.current_cust_points = 0.0
        self.setWindowTitle(f"{self.shop_name} [User: {current_user[1]}]")
        self.showFullScreen()
        self.apply_theme()
        self.setup_ui()
        self.start_online_order_sync()

    def load_config(self):
        self.config = self.db.get_business_config()
        self.shop_name = self.config[0]
        self.shop_address = self.config[1]
        self.phone1 = self.config[2]
        self.phone2 = self.config[3]
        self.whatsapp = self.config[4]
        self.gst_status = self.config[5]
        self.shop_gstin = self.config[6]
        self.upi_id = self.config[7]
        self.logo_path = self.config[8]
        self.loyalty_earn_pct = self.config[9] if len(self.config) > 9 else 1.0
        self.loyalty_point_val = self.config[10] if len(self.config) > 10 else 1.0

    def apply_theme(self):
        self.setStyleSheet("""
            QWidget { font-family: 'Segoe UI'; font-size: 12px; background-color: #f3f3f3; color: #202020; }
            QTabWidget::pane { background: #ffffff; border: 1px solid #dcdcdc; border-radius: 6px; }
            QTabBar::tab { background: #e5e5e5; padding: 8px 12px; margin-right: 2px; font-weight: bold; border-top-left-radius: 4px; border-top-right-radius: 4px; }
            QTabBar::tab:selected { background: #ffffff; color: #0078d4; border-bottom: 2px solid #0078d4; }
            QLineEdit, QComboBox, QDateEdit { background: #fff; border: 1px solid #ccc; padding: 6px; border-radius: 4px; }
            QTableWidget { background: #fff; gridline-color: #eee; border: 1px solid #ddd; }
            QHeaderView::section { background: #f0f0f0; font-weight: bold; padding: 6px; border: none; }
        """)

    def setup_ui(self):
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.tab_dash = QWidget()
        self.tab_billing = QWidget()
        self.tab_items = QWidget()
        self.tab_parties = QWidget()
        self.tab_settings = QWidget()
        self.tab_online_store = QWidget()
        self.tab_gst_reports = QWidget()
        self.tab_fin_reports = QWidget()
        self.tab_expenses = QWidget()

        self.tabs.addTab(self.tab_dash, "📊 Dashboard")
        self.tabs.addTab(self.tab_billing, "🛒 Billing [POS]")
        self.tabs.addTab(self.tab_items, "📦 Item Master & HSN")
        self.tabs.addTab(self.tab_parties, "👥 Parties (GST / Non-GST)")
        self.tabs.addTab(self.tab_settings, "⚙️ Settings & Masters")
        self.tabs.addTab(self.tab_online_store, "🌐 Online Store & Orders")
        self.tabs.addTab(self.tab_gst_reports, "📑 GST Reports")
        self.tabs.addTab(self.tab_fin_reports, "📈 P&L & Financial Reports")
        self.tabs.addTab(self.tab_expenses, "💸 Income & Expenses")

        self.init_dashboard_tab()
        self.init_billing_tab()
        self.init_items_tab()
        self.init_parties_tab()
        self.init_settings_tab()
        self.init_online_store_tab()
        self.init_gst_reports_tab()
        self.init_fin_reports_tab()
        self.init_expenses_tab()

    def init_dashboard_tab(self):
        layout = QVBoxLayout(self.tab_dash)
        layout.setContentsMargins(20, 20, 20, 20)
        title = QLabel(f"🏪 {self.shop_name} - Dashboard")
        title.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        title.setStyleSheet("color: #005fb8;")
        layout.addWidget(title)

        grid = QGridLayout()
        self.lbl_dash_sales = QLabel("₹ 0.00")
        self.lbl_dash_sales.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
        self.lbl_dash_sales.setStyleSheet("color: #107c41; background: #eafaf1; padding: 20px; border-radius: 8px;")
        
        self.lbl_dash_prods = QLabel("0")
        self.lbl_dash_prods.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
        self.lbl_dash_prods.setStyleSheet("color: #0078d4; background: #e5f1fb; padding: 20px; border-radius: 8px;")
        
        self.lbl_dash_pending = QLabel("0")
        self.lbl_dash_pending.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
        self.lbl_dash_pending.setStyleSheet("color: #d83b01; background: #fff3cd; border: 2px solid #ffc107; padding: 15px; border-radius: 8px;")

        self.lbl_dash_delivered = QLabel("0")
        self.lbl_dash_delivered.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
        self.lbl_dash_delivered.setStyleSheet("color: #107c41; background: #d4edda; border: 2px solid #28a745; padding: 15px; border-radius: 8px;")

        grid.addWidget(QLabel("<b>आज की कुल बिक्री (Today's Sales):</b>"), 0, 0)
        grid.addWidget(self.lbl_dash_sales, 1, 0)
        grid.addWidget(QLabel("<b>कुल प्रोडक्ट्स (Total Items):</b>"), 0, 1)
        grid.addWidget(self.lbl_dash_prods, 1, 1)
        grid.addWidget(QLabel("<b>🚨 पेंडिंग ऑनलाइन ऑर्डर (Pending):</b>"), 0, 2)
        grid.addWidget(self.lbl_dash_pending, 1, 2)
        grid.addWidget(QLabel("<b>✅ डिलीवर्ड ऑर्डर (Delivered):</b>"), 0, 3)
        grid.addWidget(self.lbl_dash_delivered, 1, 3)

        layout.addLayout(grid)
        layout.addStretch()
        
        btn_refresh = QPushButton("🔄 Refresh Dashboard")
        btn_refresh.setFixedWidth(180)
        btn_refresh.setStyleSheet("background-color: #0078d4; color: white; font-weight: bold; height: 35px; border-radius: 4px;")
        btn_refresh.clicked.connect(self.refresh_dashboard)
        layout.addWidget(btn_refresh)
        self.refresh_dashboard()

    def refresh_dashboard(self):
        sales, prods, low, pending_cnt, delivered_cnt = self.db.get_dashboard_stats()
        self.lbl_dash_sales.setText(f"₹ {sales:.2f}")
        self.lbl_dash_prods.setText(str(prods))
        self.lbl_dash_pending.setText(str(pending_cnt))
        self.lbl_dash_delivered.setText(str(delivered_cnt))

    def init_billing_tab(self):
        layout = QVBoxLayout(self.tab_billing)
        layout.setContentsMargins(15, 15, 15, 15)

        cust_bar = QHBoxLayout()
        self.chk_walkin = QCheckBox("Walk-in Customer")
        self.chk_walkin.setChecked(True)
        self.chk_walkin.stateChanged.connect(self.toggle_walkin)
        self.txt_cust_phone = QLineEdit()
        self.txt_cust_phone.setPlaceholderText("Customer Mobile...")
        self.txt_cust_phone.setEnabled(False)
        self.txt_cust_phone.textChanged.connect(self.check_customer)
        btn_add_c = QPushButton("+ New Party")
        btn_add_c.setStyleSheet("background-color: #107c41; color: white; font-weight: bold; border-radius: 4px; padding: 5px 10px;")
        btn_add_c.clicked.connect(lambda: (AddCustomerDialog(self.db, parent=self).exec(), self.load_parties_tables()))
        self.lbl_c_status = QLabel("Walk-in Customer")
        self.lbl_c_status.setStyleSheet("color: #005fb8; font-weight: bold;")

        cust_bar.addWidget(self.chk_walkin)
        cust_bar.addWidget(self.txt_cust_phone)
        cust_bar.addWidget(btn_add_c)
        cust_bar.addWidget(self.lbl_c_status, 2)
        layout.addLayout(cust_bar)

        self.txt_scanner = QLineEdit()
        self.txt_scanner.setPlaceholderText("Scan Barcode or Type Item Name...")
        self.txt_scanner.setStyleSheet("padding: 8px; font-size: 13px; font-weight: bold; border: 2px solid #0078d4; border-radius: 4px;")
        self.txt_scanner.returnPressed.connect(self.handle_scan)
        layout.addWidget(self.txt_scanner)

        self.grid = QTableWidget(0, 9)
        self.grid.setHorizontalHeaderLabels(["Barcode", "Item Name", "Unit Sold", "Rate", "GST%", "Stock", "Qty", "Total", "Conv"])
        self.grid.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.grid)

        bot = QHBoxLayout()
        self.lbl_sub = QLabel("Subtotal: ₹0.00 | CGST: ₹0.00 | SGST: ₹0.00")
        self.lbl_tot = QLabel("NET: ₹0.00")
        self.lbl_tot.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        self.lbl_tot.setStyleSheet("color: #107c41;")
        bot.addWidget(QLabel("Shortcuts: [F2] Search | [F10] Pay & Print"))
        bot.addStretch()
        bot.addWidget(self.lbl_sub)
        bot.addWidget(self.lbl_tot)
        layout.addLayout(bot)
        self.txt_scanner.setFocus()

    def toggle_walkin(self):
        if self.chk_walkin.isChecked():
            self.txt_cust_phone.clear()
            self.txt_cust_phone.setEnabled(False)
            self.current_cust_name = "Walk-in Customer"
            self.current_cust_phone = "WALK-IN"
            self.current_cust_points = 0.0
            self.lbl_c_status.setText("Walk-in Customer")
        else:
            self.txt_cust_phone.setEnabled(True)
            self.txt_cust_phone.setFocus()

    def check_customer(self):
        phone = self.txt_cust_phone.text().strip()
        if len(phone) >= 10:
            c = self.db.get_customer_by_phone(phone)
            if c:
                self.current_cust_name = c[1]
                self.current_cust_phone = phone
                self.current_cust_points = c[3]
                gst = f" | GSTIN: {c[5]}" if c[5] else " | Non-GST"
                self.lbl_c_status.setText(f"{c[1]} ({c[2]}) | Points: {c[3]:.1f} | Due: ₹{c[4]:.2f}{gst}")
            else:
                self.lbl_c_status.setText("New Customer")
                self.current_cust_points = 0.0

    def handle_scan(self):
        text = self.txt_scanner.text().strip()
        if not text: return
        prod = self.db.get_product_by_barcode(text)
        if prod:
            self.prompt_qty(prod)
            self.txt_scanner.clear()
        else:
            matches = self.db.search_products_by_name(text)
            if len(matches) == 1:
                self.prompt_qty(matches[0])
                self.txt_scanner.clear()
            elif len(matches) > 1:
                dlg = ItemLookupDialog(self.db, initial_search=text, parent=self)
                if dlg.exec() and dlg.selected_item:
                    full = self.db.get_product_by_id(dlg.selected_item["id"])
                    self.prompt_qty(full)
                self.txt_scanner.clear()
            else:
                QMessageBox.warning(self, "त्रुटि", "आइटम नहीं मिला!")

    def prompt_qty(self, prod):
        dlg = QuantityDialog(prod[2], prod[6], prod[7], prod[8], prod[11], prod[10], parent=self)
        if dlg.exec():
            self.add_to_grid(prod, dlg.entered_qty, dlg.selected_unit)

    def add_to_grid(self, prod, qty, unit_sold):
        for r in range(self.grid.rowCount()):
            if int(self.grid.item(r, 0).data(Qt.ItemDataRole.UserRole)) == prod[0] and self.grid.item(r, 2).text() == unit_sold:
                curr = float(self.grid.item(r, 6).text())
                self.grid.item(r, 6).setText(str(curr + qty))
                self.recalc_bill()
                return

        r = self.grid.rowCount()
        self.grid.insertRow(r)
        it_id = QTableWidgetItem(str(prod[1]))
        it_id.setData(Qt.ItemDataRole.UserRole, prod[0])
        it_id.setData(Qt.ItemDataRole.UserRole + 1, prod[8])
        
        self.grid.setItem(r, 0, it_id)
        self.grid.setItem(r, 1, QTableWidgetItem(prod[2]))
        self.grid.setItem(r, 2, QTableWidgetItem(unit_sold))
        self.grid.setItem(r, 3, QTableWidgetItem(f"{prod[10]:.2f}"))
        self.grid.setItem(r, 4, QTableWidgetItem(f"{prod[12]:.1f}%"))
        self.grid.setItem(r, 5, QTableWidgetItem(str(prod[11])))
        q_item = QTableWidgetItem(str(qty))
        self.grid.setItem(r, 6, q_item)
        tot_item = QTableWidgetItem(f"{(qty * prod[10]):.2f}")
        tot_item.setFlags(tot_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.grid.setItem(r, 7, tot_item)
        self.grid.setItem(r, 8, QTableWidgetItem(str(prod[8])))
        self.recalc_bill()

    def recalc_bill(self):
        sub = 0.0
        cgst = 0.0
        sgst = 0.0
        for r in range(self.grid.rowCount()):
            try:
                qty = float(self.grid.item(r, 6).text() or 0)
                rate = float(self.grid.item(r, 3).text() or 0)
                gst = float(self.grid.item(r, 4).text().replace('%','') or 0)
                amt = qty * rate
                sub += amt
                if self.gst_status == "REGISTERED":
                    tax = amt * (gst / 100.0)
                    cgst += tax / 2.0
                    sgst += tax / 2.0
                self.grid.item(r, 7).setText(f"{(amt + (amt * gst / 100.0) if self.gst_status == 'REGISTERED' else amt):.2f}")
            except: pass

        tax_total = (cgst + sgst) if self.gst_status == "REGISTERED" else 0.0
        net = sub + tax_total
        self.lbl_sub.setText(f"Subtotal: ₹{sub:.2f} | CGST: ₹{cgst:.2f} | SGST: ₹{sgst:.2f}")
        self.lbl_tot.setText(f"NET: ₹{net:.2f}")

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_F2:
            dlg = ItemLookupDialog(self.db, parent=self)
            if dlg.exec() and dlg.selected_item:
                self.prompt_qty(self.db.get_product_by_id(dlg.selected_item["id"]))
        elif event.key() == Qt.Key.Key_F10:
            self.checkout()
        else:
            super().keyPressEvent(event)

    def checkout(self):
        if self.grid.rowCount() == 0: return
        net_amt = float(self.lbl_tot.text().split("₹")[1])
        dlg = PaymentSettlementDialog(net_amt, self.shop_name, self.upi_id, cust_points=self.current_cust_points, pt_val=self.loyalty_point_val, parent=self)
        if not dlg.exec(): return

        items = []
        for r in range(self.grid.rowCount()):
            pid = self.grid.item(r, 0).data(Qt.ItemDataRole.UserRole)
            conv_fac = self.grid.item(r, 0).data(Qt.ItemDataRole.UserRole + 1)
            full = self.db.get_product_by_id(pid)
            items.append((pid, float(self.grid.item(r, 6).text()), self.grid.item(r, 2).text(), float(self.grid.item(r, 3).text()), full[9], float(self.grid.item(r, 4).text().replace('%','')), conv_fac))

        phone = "WALK-IN" if self.chk_walkin.isChecked() else self.txt_cust_phone.text().strip()
        sale_id, time_str, sub, cgst, sgst, red, total, earned = self.db.process_sale_with_gst(
            phone, self.current_cust_name, dlg.selected_mode, items, redeem_points=dlg.redeemed_points, earn_pct=self.loyalty_earn_pct
        )
        
        if phone != "WALK-IN" and earned > 0:
            coupon_code = self.db.generate_coupon(phone, earned * self.loyalty_point_val)
            QMessageBox.information(self, "Loyalty Coupon Generated", f"ग्राहक के लिए कूपन जेनरेट हुआ!\nकूपन कोड: {coupon_code}\n(मूल्य: ₹{earned * self.loyalty_point_val:.2f})")

        try:
            self.print_gst_receipt(sale_id, time_str, phone, self.current_cust_name, dlg.selected_mode, items, sub, cgst, sgst, total, dlg.cash_tendered)
        except Exception as e:
            QMessageBox.warning(self, "प्रिंटिंग चेतावनी", f"बिल सुरक्षित हो गया है, लेकिन प्रिंटर एरर: {str(e)}")

        self.grid.setRowCount(0)
        self.recalc_bill()
        self.refresh_dashboard()

    def print_gst_receipt(self, sale_id, time_str, phone, cname, mode, items, sub, cgst, sgst, total, tendered):
        printer = QPrinter()
        diag = QPrintDialog(printer, self)
        if not diag.exec(): return

        c_info = self.db.get_customer_by_phone(phone) if phone != "WALK-IN" else None
        c_gstin = c_info[5] if c_info and c_info[5] else "N/A"

        rows = ""
        for pid, qty, unit, rate, _, gst, ltot, _ in items:
            p = self.db.get_product_by_id(pid)
            p_name = p[2] if p else "Item"
            rows += f"<tr><td>{p_name[:12]} ({unit})</td><td align='center'>{qty}</td><td align='right'>{rate:.2f}</td><td align='right'>{ltot:.2f}</td></tr>"

        html = f"""
        <div style="font-family: monospace; font-size: 11px; width: 260px;">
            <h3 align="center" style="margin:0;">{self.shop_name}</h3>
            <p align="center" style="margin:2px;">{self.shop_address}<br>Phone: {self.phone1} | WhatsApp: {self.whatsapp}<br>GSTIN: {self.shop_gstin}</p>
            <hr>
            <p>Inv: #{sale_id} | Date: {time_str[:16]}<br>Cust: {cname}<br>Cust GSTIN: {c_gstin}<br>Pay: {mode}</p>
            <hr>
            <table width="100%" style="font-size: 10px;">
                <tr><th align="left">Item</th><th>Qty</th><th align="right">Rate</th><th align="right">Amt</th></tr>
                {rows}
            </table>
            <hr>
            Subtotal: ₹{sub:.2f}<br>CGST: ₹{cgst:.2f} | SGST: ₹{sgst:.2f}<br>
            <b>GRAND TOTAL: ₹{total:.2f}</b><br>
            {"Cash Tendered: ₹" + f"{tendered:.2f}<br>Change: ₹" + f"{max(0, tendered-total):.2f}" if mode=="CASH" else ""}
            <hr>
            <p align="center">Thank You! Visit Again!</p>
        </div>
        """
        doc = QTextDocument()
        doc.setHtml(html)
        doc.print(printer)

    # --- TAB 2: ITEM MASTER ---
    def init_items_tab(self):
        layout = QVBoxLayout(self.tab_items)
        layout.setContentsMargins(15, 15, 15, 15)
        
        form_box = QGroupBox("Add / Edit Inventory, HSN & Units")
        f_layout = QGridLayout(form_box)
        self.t_bar = QLineEdit(placeholderText="Barcode")
        self.t_name = QLineEdit(placeholderText="Item Name")
        self.t_hsn = QLineEdit(placeholderText="HSN Code")
        self.t_sub_cat = QLineEdit(placeholderText="Sub-Category")
        
        self.t_cat_combo = QComboBox()
        for c in self.db.get_all_categories(): self.t_cat_combo.addItem(c[1])

        self.t_unit_master_combo = QComboBox()
        self.update_unit_combo_items()
        self.t_unit_master_combo.currentIndexChanged.connect(self.on_unit_master_selected)

        self.t_buy = QLineEdit(placeholderText="Buy Rate")
        self.t_sale = QLineEdit(placeholderText="Sale Rate")
        self.t_stk = QLineEdit(placeholderText="Stock")
        self.c_gst = QComboBox()
        self.c_gst.addItems(["0.0", "5.0", "12.0", "18.0", "28.0"])
        
        btn_save = QPushButton("Save Product")
        btn_save.setStyleSheet("background-color: #107c41; color: white; font-weight: bold; border-radius: 4px;")
        btn_save.clicked.connect(self.save_new_product)

        f_layout.addWidget(QLabel("Barcode:"), 0, 0)
        f_layout.addWidget(self.t_bar, 0, 1)
        f_layout.addWidget(QLabel("Name:"), 0, 2)
        f_layout.addWidget(self.t_name, 0, 3)
        
        f_layout.addWidget(QLabel("Category:"), 1, 0)
        f_layout.addWidget(self.t_cat_combo, 1, 1)
        f_layout.addWidget(QLabel("Sub-Category:"), 1, 2)
        f_layout.addWidget(self.t_sub_cat, 1, 3)

        f_layout.addWidget(QLabel("HSN Code:"), 2, 0)
        f_layout.addWidget(self.t_hsn, 2, 1)
        f_layout.addWidget(QLabel("Preset Units:"), 2, 2)
        f_layout.addWidget(self.t_unit_master_combo, 2, 3)

        self.t_p_unit = QLineEdit("BOX")
        self.t_s_unit = QLineEdit("PCS")
        self.t_conv = QLineEdit("1.0")

        f_layout.addWidget(QLabel("Buy Rate:"), 3, 0)
        f_layout.addWidget(self.t_buy, 3, 1)
        f_layout.addWidget(QLabel("Sale Rate:"), 3, 2)
        f_layout.addWidget(self.t_sale, 3, 3)

        f_layout.addWidget(QLabel("Stock:"), 4, 0)
        f_layout.addWidget(self.t_stk, 4, 1)
        f_layout.addWidget(QLabel("GST %:"), 4, 2)
        f_layout.addWidget(self.c_gst, 4, 3)
        f_layout.addWidget(btn_save, 5, 3)

        layout.addWidget(form_box)

        h_bar = QHBoxLayout()
        h_bar.addWidget(QLabel("<b>Products Inventory:</b>"))
        btn_edit = QPushButton("✏ Edit Selected Product")
        btn_edit.setStyleSheet("background-color: #d83b01; color: white; font-weight: bold; border-radius: 4px; padding: 5px;")
        btn_edit.clicked.connect(self.edit_product)
        h_bar.addStretch()
        h_bar.addWidget(btn_edit)
        layout.addLayout(h_bar)

        self.tbl_items = QTableWidget(0, 13)
        self.tbl_items.setHorizontalHeaderLabels(["ID", "Barcode", "Item Name", "Category", "Sub-Cat", "HSN", "Pri. Unit", "Sec. Unit", "Conv.", "Buy", "Sale", "Stock", "GST%"])
        self.tbl_items.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.tbl_items.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl_items.doubleClicked.connect(self.edit_product)
        layout.addWidget(self.tbl_items)
        self.load_items_table()

    def update_unit_combo_items(self):
        self.t_unit_master_combo.clear()
        self.unit_presets = self.db.get_all_units_master()
        for u in self.unit_presets:
            self.t_unit_master_combo.addItem(f"{u[1]} -> {u[2]} (Conv: {u[3]})")

    def on_unit_master_selected(self, idx):
        if idx >= 0 and idx < len(self.unit_presets):
            sel = self.unit_presets[idx]
            self.t_p_unit.setText(sel[1])
            self.t_s_unit.setText(sel[2])
            self.t_conv.setText(str(sel[3]))

    def save_new_product(self):
        try:
            self.db.add_product(
                self.t_bar.text().strip(), self.t_name.text().strip(), self.t_cat_combo.currentText(),
                self.t_sub_cat.text().strip(), self.t_hsn.text().strip(), self.t_p_unit.text(), self.t_s_unit.text(),
                float(self.t_conv.text() or 1.0), float(self.t_buy.text()), float(self.t_sale.text()),
                float(self.t_stk.text()), float(self.c_gst.currentText())
            )
            QMessageBox.information(self, "सफल", "उत्पाद जुड़ गया!")
            self.load_items_table()
        except Exception as e:
            QMessageBox.warning(self, "त्रुटि", str(e))

    def edit_product(self):
        row = self.tbl_items.currentRow()
        if row < 0: return
        pid = int(self.tbl_items.item(row, 0).text())
        if EditProductDialog(self.db, pid, parent=self).exec():
            self.load_items_table()

    def load_items_table(self):
        rows = self.db.get_all_products()
        self.tbl_items.setRowCount(0)
        for r_idx, r in enumerate(rows):
            self.tbl_items.insertRow(r_idx)
            for c_idx, val in enumerate(r):
                self.tbl_items.setItem(r_idx, c_idx, QTableWidgetItem(str(val)))

    # --- TAB 3: PARTIES ---
    def init_parties_tab(self):
        layout = QVBoxLayout(self.tab_parties)
        layout.setContentsMargins(15, 15, 15, 15)
        top_bar = QHBoxLayout()
        btn_add_cust = QPushButton("+ Add Customer (GST / Non-GST)")
        btn_add_cust.setStyleSheet("background-color: #107c41; color: white; font-weight: bold; border-radius: 4px; padding: 6px 14px;")
        btn_add_cust.clicked.connect(lambda: (AddCustomerDialog(self.db, parent=self).exec(), self.load_parties_tables()))
        btn_add_supp = QPushButton("+ Add Supplier (GST / Non-GST)")
        btn_add_supp.setStyleSheet("background-color: #d83b01; color: white; font-weight: bold; border-radius: 4px; padding: 6px 14px;")
        btn_add_supp.clicked.connect(lambda: (AddSupplierDialog(self.db, parent=self).exec(), self.load_parties_tables()))
        top_bar.addWidget(btn_add_cust)
        top_bar.addWidget(btn_add_supp)
        top_bar.addStretch()
        layout.addLayout(top_bar)

        splitter_layout = QHBoxLayout()
        c_box = QGroupBox("Customers (GST & Non-GST)")
        c_layout = QVBoxLayout(c_box)
        self.tbl_custs = QTableWidget(0, 7)
        self.tbl_custs.setHorizontalHeaderLabels(["ID", "Phone", "Name", "Type", "Points", "Due (₹)", "GSTIN"])
        self.tbl_custs.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        c_layout.addWidget(self.tbl_custs)
        splitter_layout.addWidget(c_box)

        s_box = QGroupBox("Suppliers (GST & Non-GST)")
        s_layout = QVBoxLayout(s_box)
        self.tbl_supps = QTableWidget(0, 6)
        self.tbl_supps.setHorizontalHeaderLabels(["ID", "Name", "Phone", "Type", "GSTIN", "Pending (₹)"])
        self.tbl_supps.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        s_layout.addWidget(self.tbl_supps)
        splitter_layout.addWidget(s_box)
        layout.addLayout(splitter_layout)
        self.load_parties_tables()

    def load_parties_tables(self):
        custs = self.db.get_all_customers()
        self.tbl_custs.setRowCount(0)
        for i, c in enumerate(custs):
            self.tbl_custs.insertRow(i)
            vals = [c[0], c[1], c[2], c[3], f"{c[4]:.1f}", f"{c[5]:.2f}", c[6]]
            for col, val in enumerate(vals):
                self.tbl_custs.setItem(i, col, QTableWidgetItem(str(val)))

        supps = self.db.get_all_suppliers()
        self.tbl_supps.setRowCount(0)
        for i, s in enumerate(supps):
            self.tbl_supps.insertRow(i)
            vals = [s[0], s[1], s[2], s[3], s[4], f"{s[5]:.2f}"]
            for col, val in enumerate(vals):
                self.tbl_supps.setItem(i, col, QTableWidgetItem(str(val)))

    # --- TAB 4: SETTINGS & MASTERS (Business Profile Edit Added) ---
    def init_settings_tab(self):
        layout = QVBoxLayout(self.tab_settings)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.addWidget(QLabel("<b>STORE MASTERS & BUSINESS CONFIGURATION SETTINGS</b>", font=QFont("Segoe UI", 14, QFont.Weight.Bold)))

        biz_box = QGroupBox("🏪 Business Profile Settings (व्यापार विवरण एडिट करें)")
        biz_l = QGridLayout(biz_box)
        
        self.set_biz_name = QLineEdit(self.shop_name)
        self.set_biz_addr = QLineEdit(self.shop_address)
        self.set_phone1 = QLineEdit(self.phone1)
        self.set_phone2 = QLineEdit(self.phone2)
        self.set_whatsapp = QLineEdit(self.whatsapp)
        self.set_upi = QLineEdit(self.upi_id)
        self.set_gstin = QLineEdit(self.shop_gstin)
        
        self.set_gst_status = QComboBox()
        self.set_gst_status.addItems(["REGISTERED", "UNREGISTERED"])
        self.set_gst_status.setCurrentText(self.gst_status)

        btn_save_biz = QPushButton("💾 Update Business Details")
        btn_save_biz.setStyleSheet("background-color: #107c41; color: white; font-weight: bold; padding: 8px; border-radius: 4px;")
        btn_save_biz.clicked.connect(self.save_business_profile_edits)

        biz_l.addWidget(QLabel("Business Name:"), 0, 0)
        biz_l.addWidget(self.set_biz_name, 0, 1)
        biz_l.addWidget(QLabel("Address:"), 0, 2)
        biz_l.addWidget(self.set_biz_addr, 0, 3)

        biz_l.addWidget(QLabel("Primary Phone:"), 1, 0)
        biz_l.addWidget(self.set_phone1, 1, 1)
        biz_l.addWidget(QLabel("Secondary Phone:"), 1, 2)
        biz_l.addWidget(self.set_phone2, 1, 3)

        biz_l.addWidget(QLabel("WhatsApp No:"), 2, 0)
        biz_l.addWidget(self.set_whatsapp, 2, 1)
        biz_l.addWidget(QLabel("UPI ID:"), 2, 2)
        biz_l.addWidget(self.set_upi, 2, 3)

        biz_l.addWidget(QLabel("GST Status:"), 3, 0)
        biz_l.addWidget(self.set_gst_status, 3, 1)
        biz_l.addWidget(QLabel("GSTIN:"), 3, 2)
        biz_l.addWidget(self.set_gstin, 3, 3)
        biz_l.addWidget(btn_save_biz, 4, 3)

        layout.addWidget(biz_box)

        grid_settings = QGridLayout()
        grid_settings.setSpacing(15)

        cat_box = QGroupBox("Category & Sub-Category Master")
        cat_l = QVBoxLayout(cat_box)
        self.txt_new_cat = QLineEdit(placeholderText="Category Name...")
        self.txt_new_sub_cat = QLineEdit(placeholderText="Sub-Category Name...")
        btn_save_cat = QPushButton("Add Category & Sub-Category")
        btn_save_cat.setStyleSheet("background-color: #0078d4; color: white; font-weight: bold; border-radius: 4px; padding: 6px;")
        btn_save_cat.clicked.connect(self.save_new_category)
        cat_l.addWidget(QLabel("Category:"))
        cat_l.addWidget(self.txt_new_cat)
        cat_l.addWidget(QLabel("Sub-Category:"))
        cat_l.addWidget(self.txt_new_sub_cat)
        cat_l.addWidget(btn_save_cat)
        cat_l.addStretch()
        grid_settings.addWidget(cat_box, 0, 0)

        loyalty_box = QGroupBox("Loyalty Points Configuration")
        loy_l = QGridLayout(loyalty_box)
        self.txt_loyalty_pct = QLineEdit(str(self.loyalty_earn_pct))
        self.txt_loyalty_val = QLineEdit(str(self.loyalty_point_val))
        btn_save_loyalty = QPushButton("Save Loyalty Settings")
        btn_save_loyalty.setStyleSheet("background-color: #d83b01; color: white; font-weight: bold; border-radius: 4px; padding: 6px 14px;")
        btn_save_loyalty.clicked.connect(self.save_loyalty_settings)

        loy_l.addWidget(QLabel("Earn Points (%):"), 0, 0)
        loy_l.addWidget(self.txt_loyalty_pct, 0, 1)
        loy_l.addWidget(QLabel("1 Point Value (₹):"), 0, 2)
        loy_l.addWidget(self.txt_loyalty_val, 0, 3)
        loy_l.addWidget(btn_save_loyalty, 0, 4)

        layout.addLayout(grid_settings)
        layout.addWidget(loyalty_box)
        layout.addStretch()

    def save_business_profile_edits(self):
        try:
            self.db.update_business_details(
                self.set_biz_name.text().strip(), self.set_biz_addr.text().strip(),
                self.set_phone1.text().strip(), self.set_phone2.text().strip(),
                self.set_whatsapp.text().strip(), self.set_gst_status.currentText(),
                self.set_gstin.text().strip(), self.set_upi.text().strip()
            )
            self.load_config()
            self.setWindowTitle(f"{self.shop_name} [User: {self.current_user[1]}]")
            QMessageBox.information(self, "सफल", "बिजनेस प्रोफाइल सफलतापूर्वक अपडेट हो गई!")
        except Exception as e:
            QMessageBox.warning(self, "त्रुटि", str(e))

    def save_new_category(self):
        name = self.txt_new_cat.text().strip().title()
        sub = self.txt_new_sub_cat.text().strip().title()
        if not name: return
        try:
            self.db.add_category(name, sub)
            QMessageBox.information(self, "सफल", f"कैटेगरी '{name}' ({sub}) जुड़ गई!")
            self.txt_new_cat.clear()
            self.txt_new_sub_cat.clear()
        except Exception as e:
            QMessageBox.warning(self, "त्रुटि", str(e))

    def save_loyalty_settings(self):
        try:
            pct = float(self.txt_loyalty_pct.text().strip())
            pval = float(self.txt_loyalty_val.text().strip())
            self.db.update_loyalty_settings(pct, pval)
            self.load_config()
            QMessageBox.information(self, "सफल", "लॉयल्टी पॉइंट्स सेटिंग्स अपडेट हो गई!")
        except ValueError:
            QMessageBox.warning(self, "त्रुटि", "कृपया सही संख्यात्मक मान (Numbers) दर्ज करें!")

    # --- TAB 5: ONLINE STORE & SEPARATE PENDING/DELIVERED ORDERS ---
    def init_online_store_tab(self):
        layout = QVBoxLayout(self.tab_online_store)
        layout.setContentsMargins(15, 15, 15, 15)

        layout.addWidget(QLabel("<b>🌐 ONLINE STORE & CUSTOMER ORDERS MANAGEMENT</b>", font=QFont("Segoe UI", 14, QFont.Weight.Bold)))
        
        h_box = QHBoxLayout()
        
        link_box = QGroupBox("Online Store Link & Export")
        link_l = QVBoxLayout(link_box)
        link_l.addWidget(QLabel("आप नीचे दिए गए लिंक को Netlify पर अपलोड कर सकते हैं:"))
        self.txt_store_link = QLineEdit(f"https://{self.shop_name.lower().replace(' ', '')}.netlify.app")
        self.txt_store_link.setReadOnly(True)
        
        btn_copy = QPushButton("Export Online Store HTML (मात्रा चयन के साथ)")
        btn_copy.setStyleSheet("background-color: #0078d4; color: white; font-weight: bold; padding: 6px; border-radius: 4px;")
        btn_copy.clicked.connect(self.export_online_store_html)
        
        link_l.addWidget(self.txt_store_link)
        link_l.addWidget(btn_copy)
        link_l.addStretch()
        h_box.addWidget(link_box)

        coup_box = QGroupBox("Verify & Apply Customer Coupon (कूपन जांचें)")
        coup_l = QVBoxLayout(coup_box)
        self.txt_coupon_input = QLineEdit(placeholderText="Enter Coupon Code (उदा. COUPON-ABC123)")
        btn_verify = QPushButton("Verify Coupon")
        btn_verify.setStyleSheet("background-color: #107c41; color: white; font-weight: bold; padding: 6px; border-radius: 4px;")
        self.lbl_coup_result = QLabel("कूपन की स्थिति...")
        
        def verify_coup():
            code = self.txt_coupon_input.text().strip()
            amt, msg = self.db.verify_and_use_coupon(code)
            self.lbl_coup_result.setText(msg)
            if amt > 0:
                self.lbl_coup_result.setStyleSheet("color: green; font-weight: bold;")
            else:
                self.lbl_coup_result.setStyleSheet("color: red; font-weight: bold;")

        btn_verify.clicked.connect(verify_coup)
        coup_l.addWidget(self.txt_coupon_input)
        coup_l.addWidget(btn_verify)
        coup_l.addWidget(self.lbl_coup_result)
        coup_l.addStretch()
        h_box.addWidget(coup_box)

        layout.addLayout(h_box)

        orders_split = QHBoxLayout()

        # Pending Orders Box
        pend_box = QGroupBox("⏳ Pending Orders (लंबित ऑर्डर)")
        pend_l = QVBoxLayout(pend_box)
        self.tbl_pending = QTableWidget(0, 6)
        self.tbl_pending.setHorizontalHeaderLabels(["ID", "Date", "Customer", "Phone", "Items & Total", "Action"])
        self.tbl_pending.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.tbl_pending.doubleClicked.connect(lambda: self.change_order_status("DELIVERED"))
        pend_l.addWidget(self.tbl_pending)
        
        btn_mark_del = QPushButton("✅ Mark as Delivered (डिलीवर्ड करें)")
        btn_mark_del.setStyleSheet("background-color: #28a745; color: white; font-weight: bold; padding: 6px; border-radius: 4px;")
        btn_mark_del.clicked.connect(lambda: self.change_order_status("DELIVERED"))
        pend_l.addWidget(btn_mark_del)
        orders_split.addWidget(pend_box)

        # Delivered Orders Box
        del_box = QGroupBox("✅ Delivered Orders (पूर्ण ऑर्डर)")
        del_l = QVBoxLayout(del_box)
        self.tbl_delivered = QTableWidget(0, 6)
        self.tbl_delivered.setHorizontalHeaderLabels(["ID", "Date", "Customer", "Phone", "Items & Total", "Status"])
        self.tbl_delivered.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        del_l.addWidget(self.tbl_delivered)
        
        btn_refresh_ord = QPushButton("🔄 Refresh Orders")
        btn_refresh_ord.setStyleSheet("background-color: #0078d4; color: white; font-weight: bold; padding: 6px; border-radius: 4px;")
        btn_refresh_ord.clicked.connect(self.load_online_orders)
        del_l.addWidget(btn_refresh_ord)
        orders_split.addWidget(del_box)

        layout.addLayout(orders_split)

        btn_bar = QHBoxLayout()
        btn_add_manual = QPushButton("+ Add Online Order (मैनुअल आर्डर दर्ज करें)")
        btn_add_manual.setStyleSheet("background-color: #d83b01; color: white; font-weight: bold; padding: 8px; border-radius: 4px;")
        btn_add_manual.clicked.connect(lambda: (AddManualOrderDialog(self.db, parent=self).exec(), self.load_online_orders(), self.refresh_dashboard()))

        btn_wa_notify = QPushButton("💬 WhatsApp पर आर्डर भेजें")
        btn_wa_notify.setStyleSheet("background-color: #25d366; color: white; font-weight: bold; padding: 8px; border-radius: 4px;")
        btn_wa_notify.clicked.connect(self.send_order_to_whatsapp)

        btn_bar.addWidget(btn_add_manual)
        btn_bar.addWidget(btn_wa_notify)
        layout.addLayout(btn_bar)

        self.load_online_orders()

    CLOUD_API_BASE = "https://ravi-confectionery-store.onrender.com"

    def sync_online_orders_from_cloud(self, silent=True):
        """Pull website orders from the cloud into the local POS database."""
        try:
            if not CLOUD_API_KEY:
                if not silent:
                    QMessageBox.warning(
                        self,
                        "Online Sync",
                        "RAVI_POS_API_KEY Windows environment variable नहीं मिला।"
                    )
                return 0
            url = self.CLOUD_API_BASE.rstrip("/") + "/api/orders"
            req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Ravi-Confectionery-POS/1.0", "X-API-Key": CLOUD_API_KEY})
            with urllib.request.urlopen(req, timeout=12) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("status") != "success":
                raise RuntimeError(payload.get("message", "Cloud API error"))

            added = 0
            for order in payload.get("orders", []):
                if self.db.save_cloud_order_local(order):
                    added += 1

            if added:
                self.load_online_orders()
                self.refresh_dashboard()
                self.statusBar().showMessage(f"{added} नया online order प्राप्त हुआ", 8000)
            elif not silent:
                QMessageBox.information(self, "Online Orders", "कोई नया online order नहीं मिला।")
            return added
        except Exception as e:
            print("Cloud order sync error:", e)
            if not silent:
                QMessageBox.warning(self, "Online Sync", f"Online Store से connection नहीं हो पाया।\n\n{e}")
            return 0

    def sync_delivered_status_to_cloud(self, local_id):
        """Push a local Delivered action back to the cloud order."""
        try:
            if not CLOUD_API_KEY:
                print("Cloud delivered sync skipped: RAVI_POS_API_KEY is not set.")
                return
            cloud_id = self.db.get_cloud_id_for_local_order(local_id)
            if not cloud_id:
                return
            url = self.CLOUD_API_BASE.rstrip("/") + f"/api/orders/{urllib.parse.quote(str(cloud_id))}/delivered"
            req = urllib.request.Request(url, data=b"{}", method="POST", headers={"Content-Type": "application/json", "X-API-Key": CLOUD_API_KEY})
            with urllib.request.urlopen(req, timeout=12) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("status") != "success":
                print("Cloud delivered update failed:", payload)
        except Exception as e:
            print("Cloud delivered sync error:", e)

    def start_online_order_sync(self):
        self.online_sync_timer = QTimer(self)
        self.online_sync_timer.timeout.connect(lambda: self.sync_online_orders_from_cloud(True))
        self.online_sync_timer.start(15000)
        QTimer.singleShot(1500, lambda: self.sync_online_orders_from_cloud(True))

    def load_online_orders(self):
        cur = self.db.conn.cursor()
        
        try:
            cur.execute("SELECT id, order_date, customer_name, customer_phone, items_summary, total_amount FROM online_orders WHERE status = 'PENDING' ORDER BY id DESC")
            pend_rows = cur.fetchall()
            self.tbl_pending.setRowCount(0)
            for i, r in enumerate(pend_rows):
                self.tbl_pending.insertRow(i)
                vals = [str(r[0]), r[1], r[2], r[3], f"{r[4]} (₹{r[5]:.2f})", "Deliver"]
                for c, val in enumerate(vals):
                    self.tbl_pending.setItem(i, c, QTableWidgetItem(val))
        except Exception as e:
            print("Pending load error:", e)

        try:
            cur.execute("SELECT id, order_date, customer_name, customer_phone, items_summary, total_amount FROM online_orders WHERE status = 'DELIVERED' ORDER BY id DESC")
            del_rows = cur.fetchall()
            self.tbl_delivered.setRowCount(0)
            for i, r in enumerate(del_rows):
                self.tbl_delivered.insertRow(i)
                vals = [str(r[0]), r[1], r[2], r[3], f"{r[4]} (₹{r[5]:.2f})", "Delivered"]
                for c, val in enumerate(vals):
                    self.tbl_delivered.setItem(i, c, QTableWidgetItem(val))
        except Exception as e:
            print("Delivered load error:", e)

        self.refresh_dashboard()

    def change_order_status(self, new_status):
        row = self.tbl_pending.currentRow()
        if row < 0:
            QMessageBox.warning(self, "चयन करें", "कृपया पेंडिंग सूची से कोई ऑर्डर चुनें!")
            return
        order_id = int(self.tbl_pending.item(row, 0).text())
        self.db.update_order_status_db(order_id, new_status)
        self.sync_delivered_status_to_cloud(order_id)
        QMessageBox.information(self, "सफल", f"ऑर्डर #{order_id} को 'Delivered' मार्क कर दिया गया है!")
        self.load_online_orders()

    def send_order_to_whatsapp(self):
        row = self.tbl_pending.currentRow()
        if row < 0:
            row = self.tbl_delivered.currentRow()
            if row < 0:
                QMessageBox.warning(self, "चयन करें", "कृपया किसी भी सूची से कोई ऑर्डर चुनें!")
                return
            table = self.tbl_delivered
        else:
            table = self.tbl_pending

        c_name = table.item(row, 2).text()
        c_phone = table.item(row, 3).text()
        c_items = table.item(row, 4).text()

        msg = f"📦 *ऑनलाइन ऑर्डर विवरण*\n\n*ग्राहक:* {c_name}\n*मोबाइल:* {c_phone}\n*आइटम:* {c_items}"
        encoded_msg = urllib.parse.quote(msg)
        wa_url = f"https://wa.me/{self.whatsapp}?text={encoded_msg}"
        QDesktopServices.openUrl(QUrl(wa_url))

    def export_online_store_html(self):
        cur = self.db.conn.cursor()
        cur.execute("SELECT id, name, sale_rate, stock, primary_unit FROM products WHERE stock > 0")
        items = cur.fetchall()

        html_cards = ""
        for it in items:
            html_cards += f"""
            <div style="border: 1px solid #ddd; border-radius: 8px; padding: 15px; margin: 10px; width: 220px; display: inline-block; background: white; text-align: center; box-shadow: 0 2px 5px rgba(0,0,0,0.1);">
                <h3 style="margin-bottom: 5px; color: #333;">{it[1]}</h3>
                <p style="color: #107c41; font-weight: bold; font-size: 16px;">₹ {it[2]:.2f} / {it[4]}</p>
                <p style="color: gray; font-size: 12px;">उपलब्ध स्टॉक: {it[3]} {it[4]}</p>
                <button onclick="openCheckout('{it[1]}', {it[2]}, '{it[4]}')" style="background: #107c41; color: white; border: none; padding: 8px 15px; border-radius: 4px; cursor: pointer; font-weight: bold;">Order Now</button>
            </div>
            """

        store_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>{self.shop_name} - Online Store</title>
            <style>
                body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #f4f6f9; padding: 20px; margin: 0; }}
                .modal {{ display: none; position: fixed; z-index: 10; left: 0; top: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); }}
                .modal-content {{ background: white; margin: 10% auto; padding: 20px; border-radius: 8px; width: 350px; box-shadow: 0 4px 10px rgba(0,0,0,0.2); }}
                input, select, textarea {{ width: 100%; padding: 8px; margin: 8px 0; border: 1px solid #ccc; border-radius: 4px; box-sizing: border-box; }}
                .btn {{ background: #25d366; color: white; border: none; padding: 12px; width: 100%; border-radius: 4px; font-weight: bold; cursor: pointer; font-size: 14px; }}
                .close {{ float: right; font-size: 20px; font-weight: bold; cursor: pointer; color: #888; }}
            </style>
        </head>
        <body>
            <div style="text-align: center;">
                <h1>🏪 {self.shop_name}</h1>
                <p>{self.shop_address} | Phone: {self.phone1} | WhatsApp: {self.whatsapp}</p>
                <hr style="width: 50%; border: 0; border-top: 1px solid #ccc;">
            </div>
            <h2 style="text-align: center; color: #444;">Available Products (लाइव स्टॉक):</h2>
            <div style="display: flex; flex-wrap: wrap; justify-content: center;">
                {html_cards}
            </div>

            <!-- Checkout Modal -->
            <div id="checkoutModal" class="modal">
                <div class="modal-content">
                    <span class="close" onclick="closeCheckout()">&times;</span>
                    <h3>🛍️ Complete Your Order</h3>
                    <p id="prodInfo" style="font-weight: bold; color: #0078d4;"></p>
                    <label>मात्रा (Quantity):</label>
                    <input type="number" id="prodQty" value="1" min="1" oninput="calcTotal()" style="font-weight:bold;">
                    <input type="text" id="custName" placeholder="आपका पूरा नाम (Full Name)" required>
                    <input type="text" id="custPhone" placeholder="मोबाइल नंबर (Mobile Number)" required>
                    <textarea id="custAddr" placeholder="पूरा डिलीवरी पता (Delivery Address)" style="height:60px;" required></textarea>
                    <input type="text" id="couponCode" placeholder="कूपन कोड (यदि हो तो दर्ज करें)">
                    <select id="payMode">
                        <option value="UPI">UPI QR Payment</option>
                        <option value="COD">Cash on Delivery (COD)</option>
                    </select>
                    <p id="totalDisplay" style="font-weight:bold; color:#107c41; text-align:center; font-size:16px;"></p>
                    <button class="btn" onclick="submitOrderToWhatsApp()">💬 Send Order via WhatsApp</button>
                </div>
            </div>

            <script>
                let selectedProduct = "";
                let unitPrice = 0;
                let unitName = "";
                let shopWhatsApp = "{self.whatsapp}";

                function openCheckout(name, price, unit) {{
                    selectedProduct = name;
                    unitPrice = price;
                    unitName = unit;
                    document.getElementById("prodQty").value = 1;
                    document.getElementById("prodInfo").innerText = selectedProduct + " - ₹" + price + " / " + unitName;
                    calcTotal();
                    document.getElementById("checkoutModal").style.display = "block";
                }}

                function calcTotal() {{
                    let qty = document.getElementById("prodQty").value || 1;
                    let tot = qty * unitPrice;
                    document.getElementById("totalDisplay").innerText = "Total Payable: ₹ " + tot.toFixed(2);
                }}

                function closeCheckout() {{
                    document.getElementById("checkoutModal").style.display = "none";
                }}

                function submitOrderToWhatsApp() {{
                    let name = document.getElementById("custName").value;
                    let phone = document.getElementById("custPhone").value;
                    let addr = document.getElementById("custAddr").value;
                    let qty = document.getElementById("prodQty").value;
                    let coupon = document.getElementById("couponCode").value;
                    let mode = document.getElementById("payMode").value;
                    let tot = qty * unitPrice;
                    
                    if(!name || !phone || !addr) {{
                        alert("कृपया नाम, मोबाइल नंबर और पता अवश्य भरें!");
                        return;
                    }}

                    let orderSummary = selectedProduct + " x " + qty + " " + unitName;

                    // ऑटोमैटिक डेटाबेस सिंक के लिए एनग्रोक लिंक (côde में इनबिल्ट)
                    let apiURL = "https://ravi-confectionery-store.onrender.com/api/save_order";
                    
                    fetch(apiURL, {{
                        method: 'POST',
                        mode: 'no-cors',
                        headers: {{ 'Content-Type': 'application/json', 'X-API-Key': 'CHANGE_THIS_TO_YOUR_SECRET_KEY' }},
                        body: JSON.stringify({{
                            name: name,
                            phone: phone,
                            address: addr,
                            items: orderSummary,
                            total: tot,
                            mode: mode
                        }})
                    }}).catch(err => console.log("Sync error:", err));

                    let msg = "📦 *नया ऑनलाइन ऑर्डर प्राप्त हुआ!*\\n\\n" +
                              "*ग्राहक का नाम:* " + name + "\\n" +
                              "*मोबाइल नंबर:* " + phone + "\\n" +
                              "*डिलीवरी पता:* " + addr + "\\n" +
                              "*आइटम विवरण:* " + orderSummary + "\\n" +
                              "*कूपन कोड:* " + (coupon ? coupon : "लागू नहीं") + "\\n" +
                              "*कुल राशि:* ₹" + tot.toFixed(2) + "\\n" +
                              "*भुगतान का तरीका:* " + mode;
                    
                    let waUrl = "https://wa.me/" + shopWhatsApp + "?text=" + encodeURIComponent(msg);
                    window.open(waUrl, "_blank");
                    closeCheckout();
                }}
            </script>
        </body>
        </html>
        """

        path, _ = QFileDialog.getSaveFileName(self, "Export Online Store", "index.html", "HTML Files (*.html)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(store_html)
            QMessageBox.information(self, "सफल", "नया ऑनलाइन स्टोर एक्सपोर्ट हो गया है! इसे Netlify पर दोबारा ड्रैग-एंड-ड्रॉप करके अपडेट कर दें।")

    # --- TAB 6: GST REPORTS ---
    def init_gst_reports_tab(self):
        layout = QVBoxLayout(self.tab_gst_reports)
        layout.setContentsMargins(15, 15, 15, 15)

        bar = QHBoxLayout()
        self.date_gst = QDateEdit(QDate.currentDate())
        self.date_gst.setCalendarPopup(True)
        btn_gen_gst = QPushButton("Generate GSTR-1 Summary")
        btn_gen_gst.setStyleSheet("background-color: #0078d4; color: white; font-weight: bold; border-radius: 4px; padding: 6px 14px;")
        btn_gen_gst.clicked.connect(self.generate_gst_summary)

        btn_csv_gst = QPushButton("📥 Export GST Report (CSV)")
        btn_csv_gst.setStyleSheet("background-color: #107c41; color: white; font-weight: bold; border-radius: 4px; padding: 6px 14px;")
        btn_csv_gst.clicked.connect(lambda: self.export_csv_generic(self.lbl_gst_out.text(), "GST_Report.csv"))

        bar.addWidget(QLabel("Select Month/Date:"))
        bar.addWidget(self.date_gst)
        bar.addWidget(btn_gen_gst)
        bar.addWidget(btn_csv_gst)
        bar.addStretch()
        layout.addLayout(bar)

        self.lbl_gst_out = QLabel("मासिक GSTR समरी देखने के लिए तारीख चुनकर 'Generate' दबाएं...")
        self.lbl_gst_out.setFont(QFont("Courier New", 11))
        self.lbl_gst_out.setStyleSheet("background: white; padding: 20px; border: 1px solid #ccc; border-radius: 6px;")
        layout.addWidget(self.lbl_gst_out)

    def generate_gst_summary(self):
        month_str = self.date_gst.date().toString("yyyy-MM")
        cur = self.db.conn.cursor()
        cur.execute("SELECT COUNT(*), COALESCE(SUM(subtotal),0), COALESCE(SUM(cgst),0), COALESCE(SUM(sgst),0), COALESCE(SUM(total_amount),0) FROM sales WHERE date LIKE ?", (f"{month_str}%",))
        cnt, sub, cgst, sgst, tot = cur.fetchone()

        txt = f"""
================================================================================
                        GSTR-1 SALES SUMMARY ({month_str})
================================================================================
 1. कुल इनवॉइस संख्या (Total Bills)      : {cnt}
 2. कुल टैक्सेबल वैल्यू (Taxable Value)   : ₹ {sub:.2f}
 3. कुल आउटपुट CGST (Output CGST)        : ₹ {cgst:.2f}
 4. कुल आउटपुट SGST (Output SGST)        : ₹ {sgst:.2f}
 5. कुल जीएसटी देयता (Total Tax Liability): ₹ {(cgst + sgst):.2f}
 6. कुल कर सहित बिक्री (Gross Turnover)   : ₹ {tot:.2f}
================================================================================
        """
        self.lbl_gst_out.setText(txt)

    # --- TAB 7: FINANCIAL REPORTS ---
    def init_fin_reports_tab(self):
        layout = QVBoxLayout(self.tab_fin_reports)
        layout.setContentsMargins(15, 15, 15, 15)

        bar = QHBoxLayout()
        self.cmb_fin_type = QComboBox()
        self.cmb_fin_type.addItems([
            "Daily Report (दैनिक रिपोर्ट)", 
            "Monthly Report (मासिक रिपोर्ट)", 
            "Yearly Report (वार्षिक रिपोर्ट)", 
            "Profit & Loss Statement (लाभ-हानि खाता)", 
            "Balance Sheet (बैलेंस शीट)",
            "Stock Valuation (स्टॉक वैल्यूएशन)"
        ])
        self.date_fin = QDateEdit(QDate.currentDate())
        self.date_fin.setCalendarPopup(True)
        
        btn_gen_fin = QPushButton("Generate Report")
        btn_gen_fin.setStyleSheet("background-color: #0078d4; color: white; font-weight: bold; border-radius: 4px; padding: 6px 14px;")
        btn_gen_fin.clicked.connect(self.generate_financial_report)

        btn_csv_fin = QPushButton("📥 Export to Excel (CSV)")
        btn_csv_fin.setStyleSheet("background-color: #107c41; color: white; font-weight: bold; border-radius: 4px; padding: 6px 14px;")
        btn_csv_fin.clicked.connect(lambda: self.export_csv_generic(self.lbl_fin_out.text(), "Financial_Report.csv"))

        bar.addWidget(QLabel("Report Type:"))
        bar.addWidget(self.cmb_fin_type)
        bar.addWidget(QLabel("Period/Date:"))
        bar.addWidget(self.date_fin)
        bar.addWidget(btn_gen_fin)
        bar.addWidget(btn_csv_fin)
        bar.addStretch()
        layout.addLayout(bar)

        self.lbl_fin_out = QLabel("फाइनेंशियल रिपोर्ट देखने के लिए विकल्प चुनें...")
        self.lbl_fin_out.setFont(QFont("Courier New", 11))
        self.lbl_fin_out.setStyleSheet("background: white; padding: 20px; border: 1px solid #ccc; border-radius: 6px;")
        layout.addWidget(self.lbl_fin_out)

    def generate_financial_report(self):
        rtype = self.cmb_fin_type.currentText()
        dt = self.date_fin.date().toString("yyyy-MM-dd")
        cur = self.db.conn.cursor()

        if "Daily" in rtype:
            cur.execute("SELECT COUNT(*), COALESCE(SUM(subtotal),0), COALESCE(SUM(cgst+sgst),0), COALESCE(SUM(total_amount),0), COALESCE(SUM(total_profit),0) FROM sales WHERE date LIKE ?", (f"{dt}%",))
            cnt, sub, tax, tot, prof = cur.fetchone()
            cur.execute("SELECT COALESCE(SUM(amount),0) FROM cashbook WHERE date = ? AND entry_type = 'EXPENSE'", (dt,))
            exp = cur.fetchone()[0]

            txt = f"""
================================================================================
                      DAILY FINANCIAL REPORT ({dt})
================================================================================
 1. कुल बिलों की संख्या            : {cnt}
 2. टैक्सेबल बिक्री                : ₹ {sub:.2f}
 3. जीएसटी कलेक्टेड                : ₹ {tax:.2f}
 4. कुल बिक्री (Gross Revenue)    : ₹ {tot:.2f}
 5. कुल दुकान खर्चे                : ₹ {exp:.2f}
 6. शुद्ध मुनाफा (Net Profit)      : ₹ {(prof - exp):.2f}
================================================================================
            """
            self.lbl_fin_out.setText(txt)

        elif "Monthly" in rtype:
            m_str = dt[:7]
            cur.execute("SELECT COUNT(*), COALESCE(SUM(subtotal),0), COALESCE(SUM(cgst+sgst),0), COALESCE(SUM(total_amount),0), COALESCE(SUM(total_profit),0) FROM sales WHERE date LIKE ?", (f"{m_str}%",))
            cnt, sub, tax, tot, prof = cur.fetchone()
            cur.execute("SELECT COALESCE(SUM(amount),0) FROM cashbook WHERE date LIKE ? AND entry_type = 'EXPENSE'", (f"{m_str}%",))
            exp = cur.fetchone()[0]

            txt = f"""
================================================================================
                      MONTHLY FINANCIAL REPORT ({m_str})
================================================================================
 1. कुल मासिक बिल                : {cnt}
 2. टैक्सेबल वैल्यू               : ₹ {sub:.2f}
 3. कुल जीएसटी                   : ₹ {tax:.2f}
 4. कुल टर्नओवर                  : ₹ {tot:.2f}
 5. कुल मासिक खर्चे                : ₹ {exp:.2f}
 6. शुद्ध मुनाफा                  : ₹ {(prof - exp):.2f}
================================================================================
            """
            self.lbl_fin_out.setText(txt)

        elif "Yearly" in rtype:
            y_str = dt[:4]
            cur.execute("SELECT COUNT(*), COALESCE(SUM(subtotal),0), COALESCE(SUM(cgst+sgst),0), COALESCE(SUM(total_amount),0), COALESCE(SUM(total_profit),0) FROM sales WHERE date LIKE ?", (f"{y_str}%",))
            cnt, sub, tax, tot, prof = cur.fetchone()
            cur.execute("SELECT COALESCE(SUM(amount),0) FROM cashbook WHERE date LIKE ? AND entry_type = 'EXPENSE'", (f"{y_str}%",))
            exp = cur.fetchone()[0]

            txt = f"""
================================================================================
                      YEARLY FINANCIAL REPORT ({y_str})
================================================================================
 1. वार्षिक कुल बिल               : {cnt}
 2. वार्षिक टैक्सेबल वैल्यू        : ₹ {sub:.2f}
 3. वार्षिक जीएसटी                : ₹ {tax:.2f}
 4. कुल वार्षिक टर्नओवर          : ₹ {tot:.2f}
 5. कुल वार्षिक खर्चे              : ₹ {exp:.2f}
 6. वार्षिक शुद्ध मुनाफा          : ₹ {(prof - exp):.2f}
================================================================================
            """
            self.lbl_fin_out.setText(txt)

        elif "Profit & Loss" in rtype:
            cur.execute("SELECT COALESCE(SUM(total_profit),0) FROM sales")
            gross_prof = cur.fetchone()[0]
            cur.execute("SELECT COALESCE(SUM(amount),0) FROM cashbook WHERE entry_type = 'EXPENSE'")
            total_exp = cur.fetchone()[0]
            cur.execute("SELECT COALESCE(SUM(amount),0) FROM cashbook WHERE entry_type = 'INCOME'")
            other_inc = cur.fetchone()[0]
            net_prof = (gross_prof + other_inc) - total_exp

            txt = f"""
================================================================================
                      PROFIT & LOSS STATEMENT (लाभ और हानि)
================================================================================
 [INCOME / आय पक्ष]
   - सकल व्यापारिक लाभ (Gross Profit) : ₹ {gross_prof:.2f}
   - अन्य आय (Other Income)          : ₹ {other_inc:.2f}
   -----------------------------------------------------------------------------
   TOTAL INCOME                       : ₹ {(gross_prof + other_inc):.2f}

 [EXPENSES / व्यय पक्ष]
   - कुल दुकान व संचालन खर्चे          : ₹ {total_exp:.2f}
   -----------------------------------------------------------------------------
   TOTAL EXPENSES                     : ₹ {total_exp:.2f}

 ===============================================================================
   NET PROFIT (शुद्ध लाभ)             : ₹ {net_prof:.2f}
================================================================================
            """
            self.lbl_fin_out.setText(txt)

        elif "Balance Sheet" in rtype:
            cur.execute("SELECT SUM(stock * buy_rate) FROM products")
            stock_val = cur.fetchone()[0] or 0.0
            cur.execute("SELECT SUM(due_balance) FROM customers")
            cust_due = cur.fetchone()[0] or 0.0
            cur.execute("SELECT SUM(pending_balance) FROM suppliers")
            supp_due = cur.fetchone()[0] or 0.0

            txt = f"""
================================================================================
                      BALANCE SHEET (आर्थिक स्थिति)
================================================================================
 [ASSETS / संपत्ति पक्ष]
   - क्लोजिंग स्टॉक वैल्यू (Stock Value) : ₹ {stock_val:.2f}
   - ग्राहकों से लेना बाकी (Customer Due)  : ₹ {cust_due:.2f}
   -----------------------------------------------------------------------------
   TOTAL ASSETS                         : ₹ {(stock_val + cust_due):.2f}

 [LIABILITIES / देयता पक्ष]
   - सप्लायर को देना बाकी (Supplier Due) : ₹ {supp_due:.2f}
   -----------------------------------------------------------------------------
   TOTAL LIABILITIES                    : ₹ {supp_due:.2f}
================================================================================
            """
            self.lbl_fin_out.setText(txt)

        elif "Stock" in rtype:
            cur.execute("SELECT SUM(stock * buy_rate), SUM(stock * sale_rate) FROM products")
            b_val, s_val = cur.fetchone()
            txt = f"""
================================================================================
                      STOCK VALUATION REPORT
================================================================================
 1. स्टॉक की कुल खरीद मूल्य (Investment Value) : ₹ {(b_val or 0):.2f}
 2. स्टॉक की कुल बिक्री मूल्य (Market Value)   : ₹ {(s_val or 0):.2f}
 3. संभावित लाभ (Potential Profit)             : ₹ {((s_val or 0) - (b_val or 0)):.2f}
================================================================================
            """
            self.lbl_fin_out.setText(txt)

    # --- TAB 8: INCOME & EXPENSES ---
    def init_expenses_tab(self):
        layout = QVBoxLayout(self.tab_expenses)
        layout.setContentsMargins(15, 15, 15, 15)
        form = QHBoxLayout()
        self.cmb_exp_type = QComboBox()
        self.cmb_exp_type.addItems(["EXPENSE (खर्चा)", "INCOME (अन्य आय)"])
        self.cmb_exp_cat = QComboBox()
        self.cmb_exp_cat.addItems(["दुकान किराया", "बिजली बिल", "स्टाफ वेतन", "चाय-नाश्ता", "परिवहन/भाड़ा", "अन्य"])
        self.txt_exp_amt = QLineEdit()
        self.txt_exp_amt.setPlaceholderText("रकम (₹)")
        self.txt_exp_rem = QLineEdit()
        self.txt_exp_rem.setPlaceholderText("विवरण (Remark)")
        btn_exp = QPushButton("Save Record")
        btn_exp.setStyleSheet("background-color: #0078d4; color: white; font-weight: bold; border-radius: 4px; padding: 6px 14px;")
        btn_exp.clicked.connect(self.save_cashbook)

        form.addWidget(self.cmb_exp_type)
        form.addWidget(self.cmb_exp_cat)
        form.addWidget(self.txt_exp_amt)
        form.addWidget(self.txt_exp_rem)
        form.addWidget(btn_exp)
        layout.addLayout(form)

        self.exp_table = QTableWidget(0, 5)
        self.exp_table.setHorizontalHeaderLabels(["ID", "Date", "Type", "Category", "Amount (₹)"])
        self.exp_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.exp_table)
        self.load_cashbook_records()

    def save_cashbook(self):
        try:
            amt = float(self.txt_exp_amt.text())
        except ValueError: return
        etype = "EXPENSE" if "EXPENSE" in self.cmb_exp_type.currentText() else "INCOME"
        self.db.add_cashbook_entry(etype, self.cmb_exp_cat.currentText(), amt, self.txt_exp_rem.text())
        self.txt_exp_amt.clear()
        self.txt_exp_rem.clear()
        self.load_cashbook_records()

    def load_cashbook_records(self):
        cur = self.db.conn.cursor()
        cur.execute("SELECT id, date, entry_type, category, amount FROM cashbook ORDER BY id DESC LIMIT 30")
        rows = cur.fetchall()
        self.exp_table.setRowCount(0)
        for r_idx, r in enumerate(rows):
            self.exp_table.insertRow(r_idx)
            for c_idx, val in enumerate(r):
                self.exp_table.setItem(r_idx, c_idx, QTableWidgetItem(str(val)))

    def export_csv_generic(self, content_text, filename):
        path, _ = QFileDialog.getSaveFileName(self, "Export Report", filename, "CSV Files (*.csv)")
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content_text.replace("=", "").replace("\n", "\r\n"))
                QMessageBox.information(self, "सफल", "रिपोर्ट सफलतापूर्वक Excel (CSV) में सेव हो गई!")
            except Exception as e:
                QMessageBox.warning(self, "त्रुटि", str(e))


class ItemLookupDialog(QDialog):
    def __init__(self, db, initial_search="", parent=None):
        super().__init__(parent)
        self.db = db
        self.selected_item = None
        self.resize(700, 350)
        layout = QVBoxLayout(self)
        self.box = QLineEdit(initial_search)
        self.box.textChanged.connect(self.filter)
        layout.addWidget(self.box)
        self.tbl = QTableWidget(0, 7)
        self.tbl.setHorizontalHeaderLabels(["ID", "Barcode", "Name", "Category", "HSN", "Rate", "Stock"])
        self.tbl.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self.tbl)
        self.raw = db.get_all_products()
        self.pop(self.raw)

    def pop(self, rows):
        self.tbl.setRowCount(0)
        for i, r in enumerate(rows):
            self.tbl.insertRow(i)
            vals = [r[0], r[1], r[2], r[3], r[5], f"{r[10]:.2f}", str(r[11])]
            for c, v in enumerate(vals):
                self.tbl.setItem(i, c, QTableWidgetItem(str(v)))
        if self.tbl.rowCount() > 0: self.tbl.selectRow(0)

    def filter(self, txt):
        self.pop([r for r in self.raw if txt.lower() in r[2].lower() or txt in str(r[1])])

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            row = self.tbl.currentRow()
            if row >= 0:
                self.selected_item = {"id": int(self.tbl.item(row, 0).text())}
                self.accept()
        else: super().keyPressEvent(event)


if __name__ == "__main__":
    # 1. बैकग्राउंड में Flask API सर्वर शुरू करें
    server_thread = threading.Thread(target=run_flask_server, daemon=True)
    server_thread.start()

    # 2. PyQt6 Desktop Application शुरू करें
    app = QApplication(sys.argv)
    db = ConfectioneryDB()
    
    if not db.is_configured():
        setup_dlg = SetupWizardDialog(db)
        if setup_dlg.exec() != QDialog.DialogCode.Accepted:
            sys.exit(0)

    login = LoginDialog(db)
    if login.exec() == QDialog.DialogCode.Accepted:
        win = ConfectioneryMasterERP(login.user_data)
        win.show()
        sys.exit(app.exec())