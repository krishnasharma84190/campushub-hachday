from flask import Flask, render_template, request, redirect, url_for, send_file
import sqlite3, time, hmac, hashlib, io, qrcode, os, json, urllib.request

app = Flask(__name__)
SECRET = b"campushub-secret"
WINDOW = 45
BASE_URL = "http://172.168.5.74:5000"
def load_key():
    k = os.environ.get("GEMINI_API_KEY", "")
    if k:
        return k
    try:
        with open("gemini_key.txt") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""

GEMINI_KEY = load_key()
GEMINI_MODEL = "gemini-3.8-flash"
def ask_gemini(prompt):
    if not GEMINI_KEY:
        return "API key set nahi hai."
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode()
    last = ""
    for model in [GEMINI_MODEL, "gemini-3.8-flash"]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        for attempt in range(3):
            req = urllib.request.Request(url, data=body, headers={
                "Content-Type": "application/json", "x-goog-api-key": GEMINI_KEY})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = json.loads(r.read())
                return data["candidates"][0]["content"]["parts"][0]["text"]
            except urllib.error.HTTPError as e:
                last = f"AI error: HTTP {e.code}"
                if e.code in (429, 500, 503):
                    time.sleep(2)
                    continue
                break
            except Exception as e:
                last = f"AI error: {e}"
                break
    return last + " (AI abhi busy hai, thodi der baad dobara try karo)"

def db():
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    conn.execute("""CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL, date TEXT NOT NULL, venue TEXT NOT NULL)""")
    try:
        conn.execute("ALTER TABLE events ADD COLUMN description TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    conn.execute("""CREATE TABLE IF NOT EXISTS attendance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id INTEGER NOT NULL, name TEXT NOT NULL, roll TEXT NOT NULL,
        time TEXT NOT NULL, UNIQUE(event_id, roll))""")
    conn.commit()
    conn.close()

def make_token(eid, back=0):
    slot = int(time.time() // WINDOW) - back
    msg = f"{eid}:{slot}".encode()
    return hmac.new(SECRET, msg, hashlib.sha256).hexdigest()[:10]

def valid_token(eid, token):
    return token in (make_token(eid), make_token(eid, 1))

def get_event(eid):
    conn = db()
    ev = conn.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
    conn.close()
    return ev

@app.route("/")
def index():
    conn = db()
    events = conn.execute("SELECT * FROM events ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("index.html", events=events)

@app.route("/create", methods=["GET", "POST"])
def create_event():
    if request.method == "POST":
        conn = db()
        conn.execute("INSERT INTO events (title, date, venue, description) VALUES (?, ?, ?, ?)",
                     (request.form["title"], request.form["date"], request.form["venue"],
                      request.form.get("description", "")))
        conn.commit()
        conn.close()
        return redirect(url_for("index"))
    return render_template("create_event.html")

@app.route("/event/<int:eid>")
def event_page(eid):
    ev = get_event(eid)
    conn = db()
    count = conn.execute("SELECT COUNT(*) FROM attendance WHERE event_id=?", (eid,)).fetchone()[0]
    conn.close()
    return render_template("event.html", event=ev, count=count)

@app.route("/qr/<int:eid>.png")
def qr_image(eid):
    url = f"{BASE_URL}/checkin/{eid}?t={make_token(eid)}"
    buf = io.BytesIO()
    qrcode.make(url).save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png", max_age=0)

@app.route("/count/<int:eid>")
def count(eid):
    conn = db()
    c = conn.execute("SELECT COUNT(*) FROM attendance WHERE event_id=?", (eid,)).fetchone()[0]
    conn.close()
    return str(c)

@app.route("/checkin/<int:eid>", methods=["GET", "POST"])
def checkin(eid):
    ev = get_event(eid)
    token = request.values.get("t", "")
    if not valid_token(eid, token):
        return render_template("checkin.html", event=ev, error="QR expire ho gaya. Dobara scan karo.", t=token)
    if request.method == "POST":
        try:
            conn = db()
            conn.execute("INSERT INTO attendance (event_id, name, roll, time) VALUES (?, ?, ?, ?)",
                         (eid, request.form["name"].strip(), request.form["roll"].strip(),
                          time.strftime("%H:%M:%S")))
            conn.commit()
            conn.close()
            return render_template("checkin.html", event=ev, done=True, t=token)
        except sqlite3.IntegrityError:
            return render_template("checkin.html", event=ev, error="Is roll no. se attendance pehle hi lag chuki hai.", t=token)
    return render_template("checkin.html", event=ev, t=token)

@app.route("/report/<int:eid>")
def report(eid):
    ev = get_event(eid)
    conn = db()
    rows = conn.execute("SELECT * FROM attendance WHERE event_id=? ORDER BY id", (eid,)).fetchall()
    conn.close()
    return render_template("report.html", event=ev, rows=rows)

@app.route("/ai/describe")
def ai_describe():
    title = request.args.get("title", "")
    venue = request.args.get("venue", "")
    prompt = (f"Write an exciting 2-3 sentence description for a college event called '{title}' "
              f"at {venue}. Student-friendly tone. Give only the description.")
    return ask_gemini(prompt)

@app.route("/ai/insights/<int:eid>")
def ai_insights(eid):
    ev = get_event(eid)
    conn = db()
    rows = conn.execute("SELECT * FROM attendance WHERE event_id=? ORDER BY id", (eid,)).fetchall()
    conn.close()
    if not rows:
        return "Abhi koi attendance nahi hai."
    times = ", ".join(r["time"] for r in rows)
    prompt = (f"Event: {ev['title']}. Total attendees: {len(rows)}. Check-in times: {times}. "
              "Give 3 short bullet points about the attendance pattern (early or late arrivals) "
              "and 1 suggestion for the next event.")
    return ask_gemini(prompt)

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)