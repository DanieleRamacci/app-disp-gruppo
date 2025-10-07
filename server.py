# server.py
from flask import Flask, request, jsonify, session, render_template
import os, json, threading
from datetime import datetime, date, timedelta

from routes.coupon import bp_coupon

# ================== CONFIG ==================
SECRET_KEY     = "cambia-questa-chiave"   # CAMBIA in produzione
PM_PASSCODE    = "1234"                   # CAMBIA subito
PM_DATA_DIR    = "data"
PM_WEEKS_DEF   = 20
PM_MIN_P_DEF   = 4

VALID_STATUSES = {"presence", "online"}

app = Flask(__name__)
app.secret_key = SECRET_KEY
os.makedirs(PM_DATA_DIR, exist_ok=True)
app.register_blueprint(bp_coupon)

# lock per scritture concorrenti sui file JSON
write_lock = threading.Lock()

# ================== UTIL ==================
def is_tuesday(dstr: str) -> bool:
    try:
        dt = datetime.strptime(dstr, "%Y-%m-%d").date()
        return dt.weekday() == 1  # 0=Mon, 1=Tue
    except Exception:
        return False

def next_tuesdays(weeks: int):
    today = date.today()
    offset = (1 - today.weekday()) % 7  # prossimo martedì
    first = today + timedelta(days=offset)
    return [(first + timedelta(days=i*7)).strftime("%Y-%m-%d") for i in range(weeks)]

def day_path(dstr: str) -> str:
    return os.path.join(PM_DATA_DIR, f"{dstr}.json")

def read_day(dstr: str):
    """Struttura base:
    {
      "date": "YYYY-MM-DD",
      "entries": [{"name": "...", "status": "presence|online"}],
      "updated_at": "iso"
    }
    """
    p = day_path(dstr)
    if not os.path.exists(p):
        return {"date": dstr, "entries": [], "updated_at": None}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return {"date": dstr, "entries": [], "updated_at": None}
            if "entries" not in data or not isinstance(data["entries"], list):
                data["entries"] = []
            return data
    except Exception:
        return {"date": dstr, "entries": [], "updated_at": None}

def write_day(dstr: str, entry: dict):
    data = read_day(dstr)
    name = (entry.get("name") or "").strip()
    status = (entry.get("status") or "").strip()
    # rimpiazza o aggiunge l'entry per lo stesso nome
    new_entries, replaced = [], False
    for e in data["entries"]:
        if (e.get("name") or "").lower() == name.lower():
            new_entries.append({"name": name, "status": status})
            replaced = True
        else:
            new_entries.append(e)
    if not replaced:
        new_entries.append({"name": name, "status": status})
    data["entries"] = new_entries
    data["updated_at"] = datetime.utcnow().isoformat()

    with write_lock:
        with open(day_path(dstr), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    return data

def find_status(entries, name: str):
    for e in entries:
        if (e.get("name") or "").lower() == (name or "").lower():
            return e.get("status")
    return None

def sanitize_name(raw: str) -> str:
    raw = (raw or "").strip()
    raw = " ".join(raw.split())
    return raw[:60]

# ================== ROUTES: UI ==================
@app.route("/")
def home():
    user = session.get("user")
    return render_template(
        "index.html",
        user=user,
        weeks=PM_WEEKS_DEF,
        min_presence=PM_MIN_P_DEF,
        title="Presenze Martedì"
    )

# ================== ROUTES: API ==================
@app.post("/login")
def api_login():
    name = sanitize_name(request.form.get("name"))
    pwd  = (request.form.get("pass") or "").strip()
    if not name or pwd != PM_PASSCODE:
        return jsonify({"success": False, "error": "Credenziali non valide"}), 401
    session["user"] = name
    return jsonify({"success": True, "name": name})

@app.post("/logout")
def api_logout():
    session.clear()
    return jsonify({"success": True})

@app.get("/list")
def api_list():
    user = session.get("user")
    if not user:
        return jsonify({"success": False, "error": "Non autenticato"}), 401
    weeks = int(request.args.get("weeks", PM_WEEKS_DEF))
    dates = next_tuesdays(max(1, min(52, weeks)))
    rows = []
    for d in dates:
        data = read_day(d)
        # SOLO due stati
        lists = {"presence": [], "online": []}
        for e in data["entries"]:
            st = (e.get("status") or "").strip().lower()
            n  = sanitize_name(e.get("name") or "")
            if st in lists and n:
                lists[st].append(n)
        for k in lists:
            lists[k] = sorted(lists[k], key=str.lower)

        counts = {k: len(v) for k, v in lists.items()}
        mine = find_status(data["entries"], user)
        rows.append({"date": d, "counts": counts, "my": mine, "lists": lists})
    return jsonify({"success": True, "days": rows, "me": user})

@app.post("/save")
def api_save():
    user = session.get("user")
    if not user:
        return jsonify({"success": False, "error": "Non autenticato"}), 401
    d = request.form.get("date") or ""
    st = (request.form.get("status") or "").strip().lower()
    if not is_tuesday(d):
        return jsonify({"success": False, "error": "Data non valida (martedì, YYYY-MM-DD)"}), 400
    if st not in VALID_STATUSES:
        return jsonify({"success": False, "error": "Stato non valido"}), 400
    data = write_day(d, {"name": user, "status": st})
    return jsonify({"success": True, "data": data})

@app.get("/names")
def api_names():
    names = {}
    for fn in os.listdir(PM_DATA_DIR):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(PM_DATA_DIR, fn), "r", encoding="utf-8") as f:
                data = json.load(f)
                for e in data.get("entries", []):
                    n = sanitize_name(e.get("name", ""))
                    if n:
                        names[n] = True
        except Exception:
            continue
    return jsonify({"success": True, "data": sorted(names.keys(), key=str.lower)})

@app.get("/summary")
def api_summary():
    weeks = int(request.args.get("weeks", PM_WEEKS_DEF))
    dates = next_tuesdays(max(1, min(52, weeks)))
    out = []
    for d in dates:
        data = read_day(d)
        lists = {"presence": [], "online": []}
        for e in data.get("entries", []):
            n = sanitize_name(e.get("name", ""))
            s = (e.get("status") or "").strip().lower()
            if n and s in lists:
                lists[s].append(n)
        for k in lists:
            lists[k] = sorted(lists[k], key=str.lower)
        out.append({
            "date": d,
            "lists": lists,
            "counts": {k: len(v) for k, v in lists.items()}
        })
    return jsonify({"success": True, "days": out})

# ================== MAIN ==================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
