# server.py
from flask import Flask, request, jsonify, session, render_template
import os, json, threading
from datetime import datetime, date, timedelta

from routes.coupon import bp_coupon

# ================== CONFIG ==================
SECRET_KEY     = "cambia-questa-chiave"   # CAMBIA in produzione
PM_PASSCODE    = "melograno"                   # CAMBIA subito
PM_DATA_DIR    = "data"
PM_WEEKS_DEF   = 39
PM_MIN_P_DEF   = 4

VALID_STATUSES = {"presence", "online"}

# --- ADMIN ---
ADMIN_PASSCODE = "abcCBA123$miosolomio"  # CAMBIA in produzione

def require_admin():
    if not session.get("is_admin"):
        return jsonify({"success": False, "error": "Non autorizzato"}), 401


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




from flask import render_template_string, redirect, url_for

ADMIN_HTML = r"""
<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Admin Presenze</title>
<style>
  body{font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Arial;margin:0;background:#f7f9fc;color:#111827}
  .wrap{max-width:900px;margin:0 auto;padding:16px}
  .card{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:16px;margin:12px 0}
  .row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
  .btn{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:10px 14px;cursor:pointer;font-weight:600}
  .btn:hover{background:#f3f4f6}
  .danger{border-color:#ef4444;color:#ef4444}
  input,textarea{width:100%;background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:10px;font-size:16px}
  h1{font-size:22px;margin:0 0 8px 0}
  h2{font-size:18px;margin:0 0 8px 0}
  .muted{color:#6b7280}
  .sep{height:1px;background:#e5e7eb;margin:12px 0}
  .ok{color:#065f46}
</style>
</head>
<body>
<div class="wrap">
  <h1>Admin Presenze</h1>

  {% if not logged %}
    <div class="card">
      <h2>Login amministratore</h2>
      {% if msg %}<div class="muted">{{ msg }}</div>{% endif %}
      <form method="post" action="/admin" class="row" style="gap:10px;align-items:flex-end">
        <div style="flex:1;min-width:220px">
          <label>Password<br>
            <input type="password" name="pwd" placeholder="Inserisci password admin">
          </label>
        </div>
        <button class="btn" type="submit">Entra</button>
      </form>
    </div>
  {% else %}
    <div class="card">
      <div class="row" style="justify-content:space-between">
        <h2>Azioni dati</h2>
        <form method="post" action="/admin/logout"><button class="btn" type="submit">Esci</button></form>
      </div>
      <div class="sep"></div>

      <h3>Elimina persone specifiche dai JSON</h3>
      <p class="muted">Inserisci uno o più nomi, uno per riga. Verranno rimossi da <em>tutti</em> i martedì presenti nella cartella dati.</p>
      <form class="row" onsubmit="return deleteNames(event)">
        <textarea id="names" rows="5" placeholder="Mario Rossi
Giulia Bianchi"></textarea>
        <div class="row" style="gap:8px">
          <button class="btn" type="submit">Elimina nomi</button>
          <span id="out-del" class="muted"></span>
        </div>
      </form>

      <div class="sep"></div>

      <h3>Cancella TUTTI i file JSON</h3>
      <p class="muted">Cancella ogni file <code>.json</code> nella cartella dati. Operazione irreversibile.</p>
      <div class="row" style="gap:8px">
        <button class="btn danger" onclick="purgeAll()">Cancella tutto</button>
        <span id="out-purge" class="muted"></span>
      </div>

      <div class="sep"></div>

      <h3>Cancella dati locali del browser</h3>
      <p class="muted">Rimuove le chiavi salvate in localStorage (es. ultimo nome usato). Agisce solo su questo browser.</p>
      <div class="row" style="gap:8px">
        <button class="btn" onclick="clearLocal()">Cancella storage locale</button>
        <span id="out-local" class="muted"></span>
      </div>
    </div>
  {% endif %}
</div>

<script>
async function deleteNames(ev){
  ev.preventDefault();
  const names = document.getElementById('names').value.trim();
  if(!names){ alert('Inserisci almeno un nome'); return false; }
  const fd = new FormData();
  fd.append('names', names);
  const r = await fetch('/admin/delete_names', {method:'POST', body:fd});
  const j = await r.json();
  const out = document.getElementById('out-del');
  if(j.success){
    out.textContent = `Rimossi ${j.removed} record in ${j.files_touched} file.`;
    out.className = 'ok';
  }else{
    out.textContent = j.error || 'Errore';
    out.className = '';
  }
  return false;
}

async function purgeAll(){
  if(!confirm('Confermi la cancellazione di TUTTI i JSON?')) return;
  const r = await fetch('/admin/purge_all', {method:'POST'});
  const j = await r.json();
  const out = document.getElementById('out-purge');
  if(j.success){
    out.textContent = `Eliminati ${j.deleted_files} file JSON.`;
    out.className = 'ok';
  }else{
    out.textContent = j.error || 'Errore';
    out.className = '';
  }
}

function clearLocal(){
  try{
    localStorage.clear();
    sessionStorage.clear();
    document.getElementById('out-local').textContent = 'Storage locale cancellato.';
    document.getElementById('out-local').className = 'ok';
  }catch(e){
    document.getElementById('out-local').textContent = 'Impossibile cancellare storage locale.';
  }
}
</script>
</body>
</html>
"""


@app.route("/admin", methods=["GET", "POST"])
def admin_panel():
    # login admin
    if request.method == "POST" and not session.get("is_admin"):
        pwd = (request.form.get("pwd") or "").strip()
        if pwd == ADMIN_PASSCODE:
            session["is_admin"] = True
        else:
            return render_template_string(ADMIN_HTML, logged=False, msg="Password errata")

    logged = bool(session.get("is_admin"))
    return render_template_string(ADMIN_HTML, logged=logged, msg=None)

@app.post("/admin/logout")
def admin_logout():
    session.pop("is_admin", None)
    return redirect(url_for("admin_panel"))

@app.post("/admin/delete_names")
def admin_delete_names():
    if not session.get("is_admin"):
        return jsonify({"success": False, "error": "Non autorizzato"}), 401
    raw = (request.form.get("names") or "").strip()
    targets = [n.strip() for n in raw.splitlines() if n.strip()]
    if not targets:
        return jsonify({"success": False, "error": "Nessun nome fornito"}), 400

    # case-insensitive set
    targets_lower = {t.lower() for t in targets}
    removed_total = 0
    files_touched = 0

    for fn in os.listdir(PM_DATA_DIR):
        if not fn.endswith(".json"):
            continue
        p = os.path.join(PM_DATA_DIR, fn)
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue

        entries = data.get("entries", [])
        new_entries = []
        removed_here = 0
        for e in entries:
            name = (e.get("name") or "").strip()
            if name.lower() in targets_lower:
                removed_here += 1
            else:
                new_entries.append(e)

        if removed_here > 0:
            data["entries"] = new_entries
            data["updated_at"] = datetime.utcnow().isoformat()
            with write_lock:
                with open(p, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            removed_total += removed_here
            files_touched += 1

    return jsonify({"success": True, "removed": removed_total, "files_touched": files_touched})

@app.post("/admin/purge_all")
def admin_purge_all():
    if not session.get("is_admin"):
        return jsonify({"success": False, "error": "Non autorizzato"}), 401

    deleted = 0
    for fn in os.listdir(PM_DATA_DIR):
        if fn.endswith(".json"):
            try:
                os.remove(os.path.join(PM_DATA_DIR, fn))
                deleted += 1
            except Exception:
                pass
    return jsonify({"success": True, "deleted_files": deleted})


# ================== MAIN ==================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
