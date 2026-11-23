"""BuildConnect - construction materials marketplace (Flask + SQLite)."""
import os, sqlite3, secrets, json
from datetime import datetime, timedelta
from flask import send_from_directory, Flask, g, render_template, request, redirect, url_for, session, flash, abort, jsonify

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-change-me")
DB = os.environ.get("DB_PATH", "buildconnect.db")
UPLOADS = os.path.join(os.path.dirname(os.path.abspath(DB)), "uploads")
os.makedirs(UPLOADS, exist_ok=True)
app.config["MAX_CONTENT_LENGTH"] = 3 * 1024 * 1024   # 3 MB image uploads
EXTS = {".png", ".jpg", ".jpeg", ".webp"}
STAGES = ["Placed", "Confirmed", "Dispatched", "In transit", "Delivered"]
GST = {"Cement": 18, "Steel": 18, "Iron": 18, "Bricks": 5, "Sand": 5}   # % by category
UNITS = {"Cement": "50kg bag", "Steel": "tonne", "Iron": "tonne", "Bricks": "1000 pcs", "Sand": "tonne"}
CATEGORIES = list(GST)

SCHEMA = """
CREATE TABLE IF NOT EXISTS suppliers(id INTEGER PRIMARY KEY, name TEXT, city TEXT, phone TEXT,
  gstin TEXT, rating REAL DEFAULT 4.0, verified INT DEFAULT 0, created TEXT);
CREATE TABLE IF NOT EXISTS listings(id INTEGER PRIMARY KEY, supplier_id INT, category TEXT, product TEXT,
  price REAL, min_qty INT, stock INT, updated TEXT, image TEXT);
CREATE TABLE IF NOT EXISTS price_history(listing_id INT, price REAL, day TEXT);
CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY, code TEXT, user TEXT, listing_id INT, qty INT,
  address TEXT, city TEXT, subtotal REAL, discount REAL, gst REAL, total REAL, stage INT DEFAULT 0, created TEXT);
CREATE TABLE IF NOT EXISTS events(order_id INT, stage INT, note TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS quotes(id INTEGER PRIMARY KEY, user TEXT, category TEXT, qty INT, city TEXT, note TEXT, created TEXT);
"""

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB); g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close(_):
    d = g.pop("db", None)
    if d: d.close()

def q(sql, *a): return db().execute(sql, a).fetchall()
def q1(sql, *a): return db().execute(sql, a).fetchone()
def run(sql, *a):
    c = db().execute(sql, a); db().commit(); return c.lastrowid
now = lambda: datetime.now().strftime("%Y-%m-%d %H:%M")

def init():
    with sqlite3.connect(DB) as c:
        c.executescript(SCHEMA)
        if "image" not in [r[1] for r in c.execute("PRAGMA table_info(listings)")]:
            c.execute("ALTER TABLE listings ADD COLUMN image TEXT")   # migrate older databases
        if c.execute("SELECT COUNT(*) FROM suppliers").fetchone()[0]: return
        sup = [("Godavari Cements & Co", "Rajahmundry", "9800000001", "37AAAAA0000A1Z5", 4.6),
               ("Vizag Steel Traders", "Visakhapatnam", "9800000002", "37BBBBB1111B1Z6", 4.4),
               ("Sri Lakshmi Bricks", "Kakinada", "9800000003", "37CCCCC2222C1Z7", 4.2),
               ("Krishna Sand Works", "Vijayawada", "9800000004", "37DDDDD3333D1Z8", 4.5)]
        for s in sup:
            c.execute("INSERT INTO suppliers(name,city,phone,gstin,rating,verified,created) VALUES(?,?,?,?,?,1,?)", (*s, now()))
        items = [(1,"Cement","UltraTech PPC",385,50,5000),(1,"Cement","ACC OPC 53",410,50,3000),
                 (2,"Steel","TMT Fe500D 12mm",61500,1,120),(2,"Steel","TMT Fe550D 16mm",63200,1,80),(2,"Iron","MS angle 50x50x5mm",58800,1,60),(2,"Iron","Binding wire 20 gauge",72000,1,25),
                 (3,"Bricks","Red clay brick (class A)",8200,5,200),(3,"Bricks","Fly-ash brick",6400,5,300),
                 (4,"Sand","River sand",1450,10,900),(4,"Sand","M-sand (manufactured)",1100,10,1500)]
        for i, it in enumerate(items, 1):
            c.execute("INSERT INTO listings(supplier_id,category,product,price,min_qty,stock,updated) VALUES(?,?,?,?,?,?,?)", (*it, now()))
            for d in range(6, -1, -1):   # 7 days of seeded price history
                p = round(it[3] * (1 + ((i * 7 + d * 3) % 9 - 4) / 200), 2)
                c.execute("INSERT INTO price_history VALUES(?,?,?)", (i, p, (datetime.now() - timedelta(days=d)).strftime("%d %b")))

@app.context_processor
def helpers():
    return {"img": lambda r: url_for("uploaded", name=r["image"]) if r["image"] else url_for("static", filename="img/%s.svg" % r["category"].lower())}

@app.route("/uploads/<name>")
def uploaded(name): return send_from_directory(UPLOADS, name)

@app.before_request
def guest():
    session.setdefault("user", "Guest-" + secrets.token_hex(2))   # guest user for now

def discount_pct(qty, min_qty):
    r = qty / min_qty
    return 5 if r >= 10 else 3 if r >= 5 else 0

@app.route("/")
def index():
    cat, s, srt = request.args.get("cat", ""), request.args.get("q", ""), request.args.get("sort", "price")
    sql = "SELECT l.*, s.name sname, s.city, s.rating, s.verified FROM listings l JOIN suppliers s ON s.id=l.supplier_id WHERE 1=1"
    a = []
    if cat in CATEGORIES: sql += " AND category=?"; a.append(cat)
    if s: sql += " AND (product LIKE ? OR s.city LIKE ? OR s.name LIKE ?)"; a += [f"%{s}%"] * 3
    sql += {"price": " ORDER BY category, price", "rating": " ORDER BY s.rating DESC", "stock": " ORDER BY stock DESC"}.get(srt, " ORDER BY category")
    rows = q(sql, *a)
    hist = {r["id"]: [h["price"] for h in q("SELECT price FROM price_history WHERE listing_id=? ORDER BY rowid", r["id"])] for r in rows}
    return render_template("index.html", rows=rows, cat=cat, s=s, srt=srt, cats=CATEGORIES, units=UNITS, hist=hist)

@app.route("/supplier/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        f = request.form
        try:
            image, up = None, request.files.get("image")
            if up and up.filename:
                ext = os.path.splitext(up.filename)[1].lower()
                if ext not in EXTS: raise ValueError("bad image type")
                image = secrets.token_hex(8) + ext; up.save(os.path.join(UPLOADS, image))
            sid = run("INSERT INTO suppliers(name,city,phone,gstin,created) VALUES(?,?,?,?,?)",
                      f["name"].strip(), f["city"].strip(), f["phone"].strip(), f["gstin"].strip().upper(), now())
            if f.get("product") and f.get("price"):
                lid = run("INSERT INTO listings(supplier_id,category,product,price,min_qty,stock,updated,image) VALUES(?,?,?,?,?,?,?,?)",
                          sid, f["category"], f["product"], float(f["price"]), int(f.get("min_qty") or 1), int(f.get("stock") or 0), now(), image)
                run("INSERT INTO price_history VALUES(?,?,?)", lid, float(f["price"]), datetime.now().strftime("%d %b"))
        except (ValueError, KeyError):
            flash("Check the form: numbers for price and quantities, and a PNG, JPG or WEBP image."); return render_template("register.html", cats=CATEGORIES, units=UNITS)
        flash("Supplier registered. Listings are visible once verified; yours is live in demo mode.")
        return redirect(url_for("supplier", sid=sid))
    return render_template("register.html", cats=CATEGORIES, units=UNITS)

@app.route("/supplier/<int:sid>", methods=["GET", "POST"])
def supplier(sid):
    s = q1("SELECT * FROM suppliers WHERE id=?", sid) or abort(404)
    if request.method == "POST":   # update price (also logs price history)
        lid, price = int(request.form["listing"]), float(request.form["price"])
        run("UPDATE listings SET price=?, updated=? WHERE id=? AND supplier_id=?", price, now(), lid, sid)
        run("INSERT INTO price_history VALUES(?,?,?)", lid, price, datetime.now().strftime("%d %b"))
        flash("Price updated.")
    return render_template("supplier.html", s=s, rows=q("SELECT * FROM listings WHERE supplier_id=?", sid), units=UNITS)

@app.route("/order/<int:lid>", methods=["GET", "POST"])
def order(lid):
    l = q1("SELECT l.*, s.name sname, s.city scity FROM listings l JOIN suppliers s ON s.id=l.supplier_id WHERE l.id=?", lid) or abort(404)
    if request.method == "POST":
        try: qty = int(request.form["qty"])
        except ValueError: qty = 0
        if qty < l["min_qty"] or qty > l["stock"]:
            flash(f"Quantity must be between {l['min_qty']} and {l['stock']}."); return render_template("order.html", l=l, unit=UNITS[l["category"]])
        sub = qty * l["price"]; dp = discount_pct(qty, l["min_qty"]); disc = sub * dp / 100
        gst = (sub - disc) * GST[l["category"]] / 100
        code = "BC" + secrets.token_hex(3).upper()
        oid = run("INSERT INTO orders(code,user,listing_id,qty,address,city,subtotal,discount,gst,total,created) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  code, session["user"], lid, qty, request.form["address"], request.form["city"], sub, disc, gst, sub - disc + gst, now())
        run("UPDATE listings SET stock=stock-? WHERE id=?", qty, lid)
        run("INSERT INTO events VALUES(?,?,?,?)", oid, 0, "Order placed", now())
        return redirect(url_for("track", code=code))
    return render_template("order.html", l=l, unit=UNITS[l["category"]])

@app.route("/orders")
def orders():
    rows = q("SELECT o.*, l.product FROM orders o JOIN listings l ON l.id=o.listing_id WHERE user=? ORDER BY o.id DESC", session["user"])
    return render_template("orders.html", rows=rows, stages=STAGES)

def get_order(code):
    return q1("""SELECT o.*, l.product, l.category, s.name sname, s.gstin, s.city scity FROM orders o
        JOIN listings l ON l.id=o.listing_id JOIN suppliers s ON s.id=l.supplier_id WHERE code=?""", code) or abort(404)

@app.route("/track", methods=["GET", "POST"])
def track_lookup():
    if request.method == "POST": return redirect(url_for("track", code=request.form["code"].strip().upper()))
    return render_template("track_lookup.html")

@app.route("/track/<code>")
def track(code):
    o = get_order(code)
    return render_template("track.html", o=o, stages=STAGES, events=q("SELECT * FROM events WHERE order_id=? ORDER BY stage", o["id"]))

@app.route("/track/<code>/advance", methods=["POST"])
def advance(code):   # demo: simulates supplier/driver status updates
    o = get_order(code)
    if o["stage"] < len(STAGES) - 1:
        run("UPDATE orders SET stage=stage+1 WHERE id=?", o["id"])
        run("INSERT INTO events VALUES(?,?,?,?)", o["id"], o["stage"] + 1, STAGES[o["stage"] + 1], now())
    return redirect(url_for("track", code=code))

@app.route("/invoice/<code>")
def invoice(code):
    o = get_order(code)
    return render_template("invoice.html", o=o, rate=GST[o["category"]], unit=UNITS[o["category"]])

@app.route("/quote", methods=["GET", "POST"])
def quote():   # request-for-quote: post demand, suppliers respond (response flow is roadmap)
    if request.method == "POST":
        f = request.form
        run("INSERT INTO quotes(user,category,qty,city,note,created) VALUES(?,?,?,?,?,?)", session["user"], f["category"], int(f["qty"] or 0), f["city"], f["note"], now())
        flash("Quote request posted. Matching suppliers will be notified.")
    return render_template("quote.html", cats=CATEGORIES, mine=q("SELECT * FROM quotes WHERE user=? ORDER BY id DESC", session["user"]))

@app.route("/api/prices")
def api_prices():
    return jsonify([dict(r) for r in q("SELECT l.id, category, product, price, min_qty, stock, s.name supplier, s.city FROM listings l JOIN suppliers s ON s.id=l.supplier_id")])

@app.route("/healthz")
def healthz(): return "ok"

init()
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=True)
