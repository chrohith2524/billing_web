from flask import (
    Flask, render_template, request, redirect,
    url_for, send_file, session
)
import sqlite3, os
from datetime import date, datetime
from num2words import num2words
from weasyprint import HTML
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

# ---------------- APP CONFIG ----------------
app = Flask(__name__)
app.secret_key = "CHANGE_THIS_TO_A_LONG_RANDOM_SECRET"

DB = "database.db"
PDF_DIR = "invoices"
SUPPLIER_STATE_CODE = "37"  # Andhra Pradesh

os.makedirs(PDF_DIR, exist_ok=True)

# ---------------- DATABASE ----------------
def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()

    conn.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password TEXT,
        role TEXT
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT,
        name TEXT,
        hsn TEXT,
        uom TEXT,
        rate REAL,
        stock REAL
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS invoices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_no TEXT,
        invoice_date TEXT,
        customer_name TEXT,
        total REAL,
        pdf_file TEXT
    )
    """)

    conn.commit()
    conn.close()


def create_admin():
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO users (username, password, role) VALUES (?, ?, ?)",
        ("admin", generate_password_hash("admin123"), "admin")
    )
    conn.commit()
    conn.close()

# ---------------- AUTH DECORATOR ----------------
def login_required(role=None):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if "user_id" not in session:
                return redirect(url_for("login"))
            if role and session.get("role") != role:
                return "Access Denied", 403
            return f(*args, **kwargs)
        return wrapper
    return decorator

# ---------------- LOGIN ----------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE username = ?",
            (username,)
        ).fetchone()
        conn.close()

        if user and check_password_hash(user["password"], password):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            return redirect(url_for("home"))

        return render_template("login.html", error="Invalid username or password")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ---------------- DASHBOARD ----------------
@app.route("/")
@login_required()
def home():
    conn = get_db()
    invoices = conn.execute(
        "SELECT * FROM invoices ORDER BY invoice_date DESC"
    ).fetchall()
    conn.close()
    return render_template("dashboard.html", invoices=invoices)

# ---------------- STOCK ----------------
@app.route("/stock")
@login_required()
def stock():
    conn = get_db()
    products = conn.execute("SELECT * FROM products").fetchall()
    conn.close()
    return render_template("stock.html", products=products)

# ---------------- ADD PRODUCT (ADMIN) ----------------
@app.route("/add-product", methods=["GET", "POST"])
@login_required(role="admin")
def add_product():
    if request.method == "POST":
        d = request.form
        conn = get_db()
        conn.execute("""
            INSERT INTO products (code, name, hsn, uom, rate, stock)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            d["code"], d["name"], d["hsn"], d["uom"],
            float(d["rate"]), float(d["stock"])
        ))
        conn.commit()
        conn.close()
        return redirect(url_for("stock"))

    return render_template("add_product.html")

# ---------------- UPDATE STOCK (ADMIN) ----------------
@app.route("/update-stock", methods=["GET", "POST"])
@login_required(role="admin")
def update_stock():
    conn = get_db()

    if request.method == "POST":
        pid = request.form["product_id"]
        qty = float(request.form["add_qty"])
        conn.execute(
            "UPDATE products SET stock = stock + ? WHERE id = ?",
            (qty, pid)
        )
        conn.commit()
        conn.close()
        return redirect(url_for("stock"))

    products = conn.execute("SELECT * FROM products").fetchall()
    conn.close()
    return render_template("update_stock.html", products=products)

# ---------------- CREATE INVOICE ----------------
@app.route("/create_invoice", methods=["GET", "POST"])
@login_required()
def create_invoice():
    conn = get_db()
    products = conn.execute("SELECT * FROM products").fetchall()

    if request.method == "POST":
        d = request.form
        items = []
        subtotal = 0.0
        invoice_time = datetime.now().strftime("%H:%M:%S")

        for p in products:
            qty = float(d.get(f"qty_{p['id']}", 0) or 0)
            if qty > 0:
                amount = qty * p["rate"]
                subtotal += amount

                items.append({
                    "code": p["code"],
                    "name": p["name"],
                    "hsn": p["hsn"],
                    "uom": p["uom"],
                    "qty": qty,
                    "rate": p["rate"],
                    "amount": amount
                })

                conn.execute(
                    "UPDATE products SET stock = stock - ? WHERE id = ?",
                    (qty, p["id"])
                )

        Gross = subtotal / 1.18
        gst = subtotal - Gross

        if d["state_code"] == SUPPLIER_STATE_CODE:
            cgst = sgst = gst / 2
            igst = 0
        else:
            cgst = sgst = 0
            igst = gst

        amount_words = num2words(round(subtotal), lang="en").title() + " Only"
        pdf_name = f"{d['invoice_no']}.pdf"
        pdf_path = os.path.join(PDF_DIR, pdf_name)

        html = render_template(
            "invoice.html",
            invoice_no=d["invoice_no"],
            invoice_date=d["invoice_date"],
            invoice_time=invoice_time,
            customer_name=d["customer_name"],
            items=items,
            Gross=round(Gross, 2),
            cgst=round(cgst, 2),
            sgst=round(sgst, 2),
            igst=round(igst, 2),
            total=round(subtotal, 2),
            amount_words=amount_words,
        )

        HTML(string=html).write_pdf(pdf_path)

        conn.execute("""
            INSERT INTO invoices
            (invoice_no, invoice_date, customer_name, total, pdf_file)
            VALUES (?, ?, ?, ?, ?)
        """, (
            d["invoice_no"],
            d["invoice_date"],
            d["customer_name"],
            round(subtotal, 2),
            pdf_name
        ))

        conn.commit()
        conn.close()
        return html

    conn.close()
    return render_template(
        "create_invoice.html",
        products=products,
        today=date.today()
    )

# ---------------- DOWNLOAD PDF ----------------
@app.route("/download/<filename>")
@login_required()
def download_invoice(filename):
    return send_file(
        os.path.join(PDF_DIR, filename),
        as_attachment=True
    )

# ---------------- MAIN ----------------
if __name__ == "__main__":
    init_db()
    create_admin()
    app.run()
