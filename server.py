# server.py
from flask import Flask, request, jsonify, session, render_template
import os, json, threading
from datetime import datetime, date, timedelta
import re, time
from urllib.parse import urljoin
import requests
from markupsafe import Markup
from bs4 import BeautifulSoup


from routes.coupon import bp_coupon


# ================== CONFIG ==================
SECRET_KEY     = "cambia-questa-chiave"   # CAMBIA in produzione
PM_PASSCODE    = "1234"                   # CAMBIA subito
PM_DATA_DIR    = "data"
PM_WEEKS_DEF   = 20
PM_MIN_P_DEF   = 4

PROMO_URL = "https://www.italotreno.com/it/promo-week"
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
COUPON_DIR = os.path.join(STATIC_DIR, "coupons")
os.makedirs(COUPON_DIR, exist_ok=True)

# cache semplice in memoria
_coupon_cache = {"ts": 0, "data": None}
COUPON_TTL = 60 * 30  # 30 minuti


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
        counts = {"presence": 0, "flexible": 0, "remote": 0}
        for e in data["entries"]:
            st = e.get("status")
            if st in counts:
                counts[st] += 1
        mine = find_status(data["entries"], user)
        rows.append({"date": d, "counts": counts, "my": mine})
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
    if st not in ["presence", "flexible", "remote"]:
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
        lists = {"presence": [], "flexible": [], "remote": []}
        for e in data.get("entries", []):
            n = sanitize_name(e.get("name", ""))
            s = (e.get("status") or "")
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

def _download_image(abs_url: str, dest_rel: str = "coupons/promo.png") -> str:
    """Scarica l'immagine promo e la salva in static/, restituisce il path relativo per url_for('static', filename=...)"""
    resp = requests.get(abs_url, timeout=10)
    resp.raise_for_status()
    # salva come PNG/JPG a seconda del contenuto, ma manteniamo .png per semplicità
    dest_abs = os.path.join(STATIC_DIR, dest_rel)
    os.makedirs(os.path.dirname(dest_abs), exist_ok=True)
    with open(dest_abs, "wb") as f:
        f.write(resp.content)
    return dest_rel

def fetch_coupon(refresh: bool = False):
    """Legge la pagina promo, estrae: immagine, condizioni (HTML), riga scadenza, url sorgente.
       Cache 30m, forzabile con refresh=True."""
    now = time.time()
    if not refresh and _coupon_cache["data"] and now - _coupon_cache["ts"] < COUPON_TTL:
        return _coupon_cache["data"]

    r = requests.get(PROMO_URL, timeout=10)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    # 1) immagine promo (prendiamo la prima img dentro .img-container)
    img_url = None
    img_alt = ""
    first_img = soup.select_one(".img-container img")
    if first_img and first_img.get("src"):
        img_url = urljoin(PROMO_URL, first_img["src"])
        img_alt = first_img.get("alt", "")
    # se non trovata, fallback: nulla

    # 2) condizioni (inner HTML di .condizioni-text, se esiste)
    cond_el = soup.select_one(".condizioni-text")
    conditions_html = ""
    expires_text = None
    if cond_el:
        # HTML come stringa "sicura" (verrà messa in safe nel template)
        # Rimuovi commenti HTML e spazi ripetuti
        for c in cond_el.find_all(string=lambda s: isinstance(s, type(cond_el.string)) and isinstance(s, str) and s.strip().startswith("<!--")):
            try:
                c.extract()
            except Exception:
                pass
        conditions_html = str(cond_el)

        # prova ad individuare la riga "Acquista entro..."
        strongs = cond_el.find_all("strong")
        for st in strongs:
            txt = st.get_text(strip=True)
            if "Acquista entro" in txt or "Promozione estesa" in txt:
                expires_text = txt
                break

    # 3) scarica l’immagine in locale (se disponibile)
    local_img_rel = None
    if img_url:
        try:
            local_img_rel = _download_image(img_url)  # es. "coupons/promo.png"
        except Exception:
            local_img_rel = None

    data = {
        "source_url": PROMO_URL,
        "local_img_rel": local_img_rel,  # per url_for('static', filename=...)
        "img_alt": img_alt or "Italo Promo",
        "conditions_html": conditions_html,
        "expires_text": expires_text,
        "fetched_at": datetime.now().strftime("%d/%m/%Y %H:%M"),
    }
    _coupon_cache["ts"] = now
    _coupon_cache["data"] = data
    return data


@app.get("/coupon")
def coupon_page():
    refresh = request.args.get("refresh") == "1"
    try:
        data = fetch_coupon(refresh=refresh)
    except Exception as e:
        # fallback in caso di errore
        data = {
            "source_url": PROMO_URL,
            "local_img_rel": None,
            "img_alt": "Italo Promo",
            "conditions_html": "<p class='pm-muted'>Non è stato possibile caricare le condizioni ora. Riprova più tardi.</p>",
            "expires_text": None,
            "fetched_at": datetime.now().strftime("%d/%m/%Y %H:%M"),
        }

    # costruisci la URL dell’immagine locale (se presente)
    img_url = url_for("static", filename=data["local_img_rel"]) if data["local_img_rel"] else None
    return render_template(
        "coupon.html",
        title="Promo Week Italo",
        img_url=img_url,
        img_alt=data["img_alt"],
        conditions_html=Markup(data["conditions_html"] or ""),
        expires_text=data["expires_text"],
        source_url=data["source_url"],
        fetched_at=data["fetched_at"],
    )


# ================== MAIN ==================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
