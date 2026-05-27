from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    send_file,
    session
)

import sqlite3
import os

from datetime import date, datetime

from num2words import num2words
from weasyprint import HTML

from werkzeug.security import (
    generate_password_hash,
    check_password_hash
)

from functools import wraps


# ================= CONFIG =================

DB = "database.db"

PDF_DIR = "invoices"

SUPPLIER_STATE_CODE = "37"   # Andhra Pradesh

os.makedirs(PDF_DIR, exist_ok=True)


# ================= INDIAN CURRENCY FORMAT =================

def indian_currency(number):

    number = float(number)

    integer, decimal = f"{number:.2f}".split(".")

    integer = integer[::-1]

    parts = []

    parts.append(integer[:3])

    integer = integer[3:]

    while integer:

        parts.append(integer[:2])

        integer = integer[2:]

    formatted = ",".join(parts)[::-1]

    return f"{formatted}.{decimal}"


# ================= APP =================

app = Flask(__name__)

app.secret_key = os.environ.get(
    "SECRET_KEY",
    "dev-secret-key"
)

app.jinja_env.globals.update(
    indian_currency=indian_currency
)


# ================= DATABASE =================

def get_db():

    conn = sqlite3.connect(DB)

    conn.row_factory = sqlite3.Row

    return conn


def init_db():

    conn = get_db()

    # USERS
    conn.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password TEXT,
        role TEXT
    )
    """)

    # PRODUCTS
    conn.execute("""
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT,
        name TEXT,
        hsn TEXT,
        gst REAL DEFAULT 18,
        size TEXT DEFAULT '',
        uom TEXT,
        rate REAL,
        stock REAL
    )
    """)

    # INVOICES
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

    # INVOICE ITEMS
    conn.execute("""
    CREATE TABLE IF NOT EXISTS invoice_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_id INTEGER,
        product_code TEXT,
        product_name TEXT,
        hsn TEXT,
        qty REAL,
        rate REAL,
        amount REAL
    )
    """)

    conn.commit()

    conn.close()


# ================= CREATE ADMIN =================

def create_admin():

    conn = get_db()

    conn.execute(
        """
        INSERT OR IGNORE INTO users
        (username, password, role)
        VALUES (?, ?, ?)
        """,
        (
            "admin",
            generate_password_hash("admin123"),
            "admin"
        )
    )

    conn.commit()

    conn.close()


# ================= AUTO INVOICE NUMBER =================

def generate_invoice_number():

    conn = get_db()

    last = conn.execute(
        """
        SELECT id
        FROM invoices
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()

    conn.close()

    next_id = 1 if not last else last["id"] + 1

    year = datetime.now().year

    return f"VK-{year}-{next_id:04d}"


# ================= AUTH DECORATOR =================

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


# ================= LOGIN =================

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        username = request.form["username"]

        password = request.form["password"]

        conn = get_db()

        user = conn.execute(
            """
            SELECT *
            FROM users
            WHERE username = ?
            """,
            (username,)
        ).fetchone()

        conn.close()

        if user and check_password_hash(
            user["password"],
            password
        ):

            session["user_id"] = user["id"]

            session["username"] = user["username"]

            session["role"] = user["role"]

            return redirect(url_for("home"))

        return render_template(
            "login.html",
            error="Invalid username or password"
        )

    return render_template("login.html")


# ================= LOGOUT =================

@app.route("/logout")
def logout():

    session.clear()

    return redirect(url_for("login"))


# ================= DASHBOARD =================

@app.route("/")
@login_required()
def home():

    conn = get_db()

    invoices = conn.execute(
        """
        SELECT *
        FROM invoices
        ORDER BY invoice_date DESC
        """
    ).fetchall()

    conn.close()

    return render_template(
        "dashboard.html",
        invoices=invoices
    )


# ================= STOCK =================

@app.route("/stock")
@login_required()
def stock():

    conn = get_db()

    products = conn.execute(
        "SELECT * FROM products"
    ).fetchall()

    conn.close()

    return render_template(
        "stock.html",
        products=products
    )


# ================= ADD PRODUCT =================

@app.route("/add-product", methods=["GET", "POST"])
@login_required(role="admin")
def add_product():

    if request.method == "POST":

        d = request.form

        conn = get_db()

        conn.execute("""
            INSERT INTO products
            (
                code,
                name,
                hsn,
                gst,
                size,
                uom,
                rate,
                stock
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (

            d["code"],

            d["name"],

            d["hsn"],

            float(d.get("gst", 18)),

            d.get("size", ""),

            d["uom"],

            float(d["rate"]),

            float(d["stock"])

        ))

        conn.commit()

        conn.close()

        return redirect(url_for("stock"))

    return render_template("add_product.html")


# ================= UPDATE STOCK =================

@app.route("/update-stock", methods=["GET", "POST"])
@login_required(role="admin")
def update_stock():

    conn = get_db()

    if request.method == "POST":

        pid = request.form["product_id"]

        qty = float(request.form["add_qty"])

        conn.execute(
            """
            UPDATE products
            SET stock = stock + ?
            WHERE id = ?
            """,
            (qty, pid)
        )

        conn.commit()

        conn.close()

        return redirect(url_for("stock"))

    products = conn.execute(
        "SELECT * FROM products"
    ).fetchall()

    conn.close()

    return render_template(
        "update_stock.html",
        products=products
    )


# ================= CREATE INVOICE =================

@app.route("/create_invoice", methods=["GET", "POST"])
@login_required()
def create_invoice():

    conn = get_db()

    products = conn.execute(
        "SELECT * FROM products"
    ).fetchall()

    if request.method == "POST":

        d = request.form

        items = []

        subtotal = 0.0

        invoice_time = datetime.now().strftime("%H:%M:%S")

        # ===== PRODUCTS =====

        for p in products:

            qty = float(
                d.get(f"qty_{p['id']}", 0) or 0
            )

            if qty > p["stock"]:

                conn.close()

                return f"Insufficient stock for {p['name']}"

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
                    """
                    UPDATE products
                    SET stock = stock - ?
                    WHERE id = ?
                    """,
                    (qty, p["id"])
                )

        # ===== GST =====

        Gross = subtotal / 1.18

        gst = subtotal - Gross

        if d["state_code"] == SUPPLIER_STATE_CODE:

            cgst = gst / 2

            sgst = gst / 2

            igst = 0

        else:

            cgst = 0

            sgst = 0

            igst = gst

        # ===== WORDS =====

        amount_words = (
            num2words(
                round(subtotal),
                lang="en_IN"
            ).title()
            + " Only"
)

        # ===== PDF =====

        pdf_name = f"{d['invoice_no']}.pdf"

        pdf_path = os.path.join(
            PDF_DIR,
            pdf_name
        )

        html = render_template(

            "invoice.html",

            invoice_no=d["invoice_no"],

            invoice_date=d["invoice_date"],

            invoice_time=invoice_time,

            customer_name=d["customer_name"],

            address=d.get("billing_address", ""),

            phone=d.get("phone", ""),

            state_code=d.get("state_code", ""),

            payment_term=d.get("payment_term", ""),

            place_of_supply=d.get("place_of_supply", ""),

            vehicle_no=d.get("vehicle_no", ""),

            bill_type=d.get("bill_type", ""),

            party_contact=d.get("party_contact", ""),

            shipping_name=d.get("shipping_name", ""),

            shipping_address=d.get("shipping_address", ""),

            shipping_phone=d.get("shipping_phone", ""),

            shipping_state=d.get("shipping_state", ""),

            items=items,

            Gross=round(Gross, 2),

            cgst=round(cgst, 2),

            sgst=round(sgst, 2),

            igst=round(igst, 2),

            total=round(subtotal, 2),

            amount_words=amount_words,

            pdf_file=pdf_name
        )

        HTML(string=html).write_pdf(pdf_path)

        # ===== SAVE INVOICE =====

        conn.execute("""
            INSERT INTO invoices
            (
                invoice_no,
                invoice_date,
                customer_name,
                total,
                pdf_file
            )
            VALUES (?, ?, ?, ?, ?)
        """, (

            d["invoice_no"],

            d["invoice_date"],

            d["customer_name"],

            round(subtotal, 2),

            pdf_name

        ))

        invoice_id = conn.execute(
            "SELECT last_insert_rowid()"
        ).fetchone()[0]

        # ===== SAVE ITEMS =====

        for item in items:

            conn.execute("""
                INSERT INTO invoice_items
                (
                    invoice_id,
                    product_code,
                    product_name,
                    hsn,
                    qty,
                    rate,
                    amount
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (

                invoice_id,

                item["code"],

                item["name"],

                item["hsn"],

                item["qty"],

                item["rate"],

                item["amount"]

            ))

        conn.commit()

        conn.close()

        return html

    conn.close()

    return render_template(

        "create_invoice.html",

        products=products,

        today=date.today(),

        invoice_no=generate_invoice_number()
    )


# ================= DOWNLOAD PDF =================

@app.route("/download/<filename>")
@login_required()
def download_invoice(filename):

    return send_file(

        os.path.join(PDF_DIR, filename),

        as_attachment=True
    )


# ================= START =================

init_db()

create_admin()

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
    )