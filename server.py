# server.py
from flask import Flask, request, jsonify, session, render_template
import os, json, threading, io, zipfile, re
from datetime import datetime, date, timedelta

from routes.coupon import bp_coupon

# ================== CONFIG ==================
SECRET_KEY     = "cambia-questa-chiave"   # CAMBIA in produzione
PM_PASSCODE    = "melograno"                   # CAMBIA subito
PM_DATA_DIR    = "data"
PM_PAUSE_FILE  = "pauses.json"
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

def pause_path() -> str:
    return os.path.join(PM_DATA_DIR, PM_PAUSE_FILE)

def read_pauses():
    p = pause_path()
    if not os.path.exists(p):
        return {"paused_dates": [], "updated_at": None}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {"paused_dates": [], "updated_at": None}

    raw = data.get("paused_dates", []) if isinstance(data, dict) else []
    out = []
    seen = set()
    for d in raw:
        ds = (d or "").strip()
        if is_tuesday(ds) and ds not in seen:
            out.append(ds)
            seen.add(ds)
    out.sort()
    return {"paused_dates": out, "updated_at": data.get("updated_at") if isinstance(data, dict) else None}

def write_pauses(paused_dates):
    valid = normalize_paused_dates(paused_dates)
    payload = {"paused_dates": valid, "updated_at": datetime.utcnow().isoformat()}
    with write_lock:
        with open(pause_path(), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    return payload

def normalize_paused_dates(paused_dates):
    valid = []
    seen = set()
    for d in paused_dates:
        ds = (d or "").strip()
        if is_tuesday(ds) and ds not in seen:
            valid.append(ds)
            seen.add(ds)
    valid.sort()
    return valid

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
    paused_dates = set(read_pauses().get("paused_dates", []))
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
        rows.append({"date": d, "counts": counts, "my": mine, "lists": lists, "paused": d in paused_dates})
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
    if d in set(read_pauses().get("paused_dates", [])):
        return jsonify({"success": False, "error": "Martedì in pausa: non è possibile segnare la presenza"}), 409
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
    paused_dates = set(read_pauses().get("paused_dates", []))
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
            "counts": {k: len(v) for k, v in lists.items()},
            "paused": d in paused_dates,
        })
    return jsonify({"success": True, "days": out})




from flask import render_template_string, redirect, url_for, send_file

DAY_JSON_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")
PAUSE_ARCHIVE_FILE = "_pauses.json"

def list_day_json_files():
    files = []
    for fn in os.listdir(PM_DATA_DIR):
        if DAY_JSON_RE.match(fn):
            files.append(fn)
    return sorted(files)

def normalize_day_payload(dstr: str, payload):
    if not isinstance(payload, dict):
        raise ValueError(f"Formato non valido per {dstr}")
    entries = payload.get("entries", [])
    if not isinstance(entries, list):
        raise ValueError(f"'entries' non valido per {dstr}")

    normalized = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = sanitize_name(entry.get("name", ""))
        status = (entry.get("status") or "").strip().lower()
        if not name or status not in VALID_STATUSES:
            continue
        key = name.lower()
        if key in seen:
            normalized = [x for x in normalized if x["name"].lower() != key]
        normalized.append({"name": name, "status": status})
        seen.add(key)
    return {"date": dstr, "entries": normalized, "updated_at": datetime.utcnow().isoformat()}

def build_zip_bytes(days_data: dict, generated_by: str, pauses_data=None):
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        manifest = {
            "generated_at": datetime.utcnow().isoformat(),
            "generated_by": generated_by,
            "files": sorted(days_data.keys()),
            "has_pauses": bool(pauses_data),
            "format_version": 1,
        }
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for fn, content in sorted(days_data.items()):
            zf.writestr(fn, json.dumps(content, ensure_ascii=False, indent=2))
        if pauses_data:
            zf.writestr(PAUSE_ARCHIVE_FILE, json.dumps(pauses_data, ensure_ascii=False, indent=2))
    payload.seek(0)
    return payload

def read_backup_zip(file_storage):
    raw = file_storage.read()
    if not raw:
        raise ValueError("File di backup vuoto")

    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise ValueError("Backup non valido: file ZIP corrotto") from exc

    days = {}
    pauses_data = None
    with zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
        for member in names:
            base = os.path.basename(member)
            if base == PAUSE_ARCHIVE_FILE:
                with zf.open(member, "r") as f:
                    try:
                        payload = json.loads(f.read().decode("utf-8"))
                    except Exception as exc:
                        raise ValueError("JSON non valido nel file pause") from exc
                paused_dates = payload.get("paused_dates", []) if isinstance(payload, dict) else []
                pauses_data = {
                    "paused_dates": normalize_paused_dates(paused_dates),
                    "updated_at": datetime.utcnow().isoformat(),
                }
                continue
            if not DAY_JSON_RE.match(base):
                continue
            with zf.open(member, "r") as f:
                try:
                    payload = json.loads(f.read().decode("utf-8"))
                except Exception as exc:
                    raise ValueError(f"JSON non valido nel file {base}") from exc
            day_str = base.replace(".json", "")
            days[base] = normalize_day_payload(day_str, payload)

    if not days and pauses_data is None:
        raise ValueError("Nessun file giornaliero valido trovato nel backup")
    if pauses_data is None:
        pauses_data = {"paused_dates": [], "updated_at": None}
    return days, pauses_data

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
  .tbl{width:100%;border-collapse:collapse;font-size:14px}
  .tbl th,.tbl td{border-bottom:1px solid #e5e7eb;padding:8px 6px;text-align:left;vertical-align:top}
  .badge{display:inline-block;padding:4px 10px;border-radius:999px;border:1px solid #e5e7eb;background:#fff}
  .badge.pause{border-color:#ef4444;color:#b91c1c;background:#fef2f2}
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

      <h3>Calendario pause</h3>
      <p class="muted">Gestisci le eccezioni: martedì in pausa (nessuna prenotazione consentita).</p>
      <div style="overflow:auto">
        <table class="tbl">
          <thead>
            <tr>
              <th>Martedì futuri</th>
              <th>Stato</th>
              <th>Azione</th>
            </tr>
          </thead>
          <tbody id="pause-active-body">
            <tr><td colspan="3" class="muted">Caricamento...</td></tr>
          </tbody>
        </table>
      </div>

      <div class="sep"></div>

      <h3>Martedì in pausa (da oggi in avanti)</h3>
      <div style="overflow:auto">
        <table class="tbl">
          <thead>
            <tr>
              <th>Data</th>
              <th>Distanza</th>
            </tr>
          </thead>
          <tbody id="pause-list-body">
            <tr><td colspan="2" class="muted">Caricamento...</td></tr>
          </tbody>
        </table>
      </div>

      <div class="sep"></div>

      <h3>Backup dati</h3>
      <p class="muted">Scarica un backup ZIP completo dei JSON correnti.</p>
      <div class="row" style="gap:8px">
        <a class="btn" href="/admin/backup/download">Scarica backup</a>
        <span id="out-backup" class="muted"></span>
      </div>

      <div class="sep"></div>

      <h3>Ripristina da backup ZIP</h3>
      <p class="muted">
        Modalità <strong>Merge</strong> (consigliata): aggiunge/aggiorna dati dal backup senza cancellare i JSON attuali.<br>
        Modalità <strong>Replace</strong>: sostituisce completamente i dati correnti con quelli del backup.
      </p>
      <form class="row" onsubmit="return restoreBackup(event)">
        <input id="backup-file" type="file" accept=".zip,application/zip">
        <select id="restore-mode" style="padding:10px;border:1px solid #e5e7eb;border-radius:10px">
          <option value="merge">Merge (sicuro)</option>
          <option value="replace">Replace (sostituisce tutto)</option>
        </select>
        <div class="row" style="gap:8px">
          <button class="btn" type="submit">Ripristina backup</button>
          <span id="out-restore" class="muted"></span>
        </div>
      </form>

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
function fmtDateIt(iso){
  try{
    return new Date(iso).toLocaleDateString('it-IT',{weekday:'long',day:'2-digit',month:'long',year:'numeric'});
  }catch(_){
    return iso;
  }
}

function daysDistanceLabel(iso){
  const now = new Date();
  now.setHours(0,0,0,0);
  const d = new Date(iso);
  d.setHours(0,0,0,0);
  const diff = Math.round((d - now) / 86400000);
  if(diff <= 0) return 'oggi';
  if(diff === 1) return 'domani';
  return `tra ${diff} giorni`;
}

async function togglePause(dateStr, paused){
  const fd = new FormData();
  fd.append('date', dateStr);
  fd.append('paused', paused ? '1' : '0');
  const r = await fetch('/admin/pauses/set', {method:'POST', body:fd});
  const j = await r.json();
  if(!j.success){
    alert(j.error || 'Errore');
    return;
  }
  await loadPauseDashboard();
}

async function loadPauseDashboard(){
  const activeBody = document.getElementById('pause-active-body');
  const listBody = document.getElementById('pause-list-body');
  if(!activeBody || !listBody) return;

  const r = await fetch('/admin/pauses?weeks=52');
  const j = await r.json();
  if(!j.success){
    activeBody.innerHTML = '<tr><td colspan="3">Errore caricamento</td></tr>';
    listBody.innerHTML = '<tr><td colspan="2">Errore caricamento</td></tr>';
    return;
  }

  if(!j.active_tuesdays.length){
    activeBody.innerHTML = '<tr><td colspan="3" class="muted">Nessun martedì futuro.</td></tr>';
  }else{
    activeBody.innerHTML = j.active_tuesdays.map(row => `
      <tr>
        <td>${fmtDateIt(row.date)}<div class="muted">${row.date}</div></td>
        <td>${row.paused ? '<span class="badge pause">Pausa</span>' : '<span class="badge">Attivo</span>'}</td>
        <td>
          <button class="btn ${row.paused ? '' : 'danger'}" onclick="togglePause('${row.date}', ${row.paused ? 'false' : 'true'})">
            ${row.paused ? 'Rimuovi pausa' : 'Segna pausa'}
          </button>
        </td>
      </tr>
    `).join('');
  }

  if(!j.paused_from_today.length){
    listBody.innerHTML = '<tr><td colspan="2" class="muted">Nessuna pausa futura.</td></tr>';
  }else{
    listBody.innerHTML = j.paused_from_today.map(row => `
      <tr>
        <td>${fmtDateIt(row.date)}<div class="muted">${row.date}</div></td>
        <td>${daysDistanceLabel(row.date)}</td>
      </tr>
    `).join('');
  }
}

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

async function restoreBackup(ev){
  ev.preventDefault();
  const fileInput = document.getElementById('backup-file');
  const modeInput = document.getElementById('restore-mode');
  const out = document.getElementById('out-restore');
  const file = fileInput.files?.[0];
  if(!file){ alert('Seleziona un file ZIP'); return false; }
  const mode = modeInput.value || 'merge';
  if(mode === 'replace' && !confirm('Confermi REPLACE? I dati correnti verranno sostituiti.')){
    return false;
  }
  const fd = new FormData();
  fd.append('backup', file);
  fd.append('mode', mode);
  const r = await fetch('/admin/backup/restore', {method:'POST', body:fd});
  const j = await r.json();
  if(j.success){
    out.textContent = `Ripristino completato (${j.mode}). File importati: ${j.imported_files}. Pause importate: ${j.paused_imported}. Backup di sicurezza: ${j.pre_restore_backup}.`;
    out.className = 'ok';
  }else{
    out.textContent = j.error || 'Errore';
    out.className = '';
  }
  return false;
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

loadPauseDashboard();
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

@app.get("/admin/pauses")
def admin_pauses():
    if not session.get("is_admin"):
        return jsonify({"success": False, "error": "Non autorizzato"}), 401

    try:
        weeks = int(request.args.get("weeks", 52))
    except Exception:
        weeks = 52
    weeks = max(1, min(156, weeks))
    future_tuesdays = next_tuesdays(weeks)
    paused_dates = set(read_pauses().get("paused_dates", []))

    active_rows = [{"date": d, "paused": d in paused_dates} for d in future_tuesdays]

    today_str = date.today().strftime("%Y-%m-%d")
    paused_future = sorted([d for d in paused_dates if d >= today_str])
    paused_rows = []
    for d in paused_future:
        try:
            dd = datetime.strptime(d, "%Y-%m-%d").date()
            delta = (dd - date.today()).days
        except Exception:
            delta = None
        paused_rows.append({"date": d, "distance_days": delta})

    return jsonify({"success": True, "active_tuesdays": active_rows, "paused_from_today": paused_rows})

@app.post("/admin/pauses/set")
def admin_set_pause():
    if not session.get("is_admin"):
        return jsonify({"success": False, "error": "Non autorizzato"}), 401

    dstr = (request.form.get("date") or "").strip()
    raw_paused = (request.form.get("paused") or "0").strip().lower()
    paused = raw_paused in {"1", "true", "yes", "on"}

    if not is_tuesday(dstr):
        return jsonify({"success": False, "error": "Data non valida (martedì, YYYY-MM-DD)"}), 400

    current = set(read_pauses().get("paused_dates", []))
    if paused:
        current.add(dstr)
    else:
        current.discard(dstr)

    saved = write_pauses(sorted(current))
    return jsonify({"success": True, "paused_dates": saved.get("paused_dates", [])})

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

@app.get("/admin/backup/download")
def admin_backup_download():
    if not session.get("is_admin"):
        return jsonify({"success": False, "error": "Non autorizzato"}), 401

    days = {}
    for fn in list_day_json_files():
        dstr = fn[:-5]
        days[fn] = normalize_day_payload(dstr, read_day(dstr))
    pauses = read_pauses()

    archive = build_zip_bytes(days, generated_by="admin", pauses_data=pauses)
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    return send_file(
        archive,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"backup-presenze-{stamp}.zip",
    )

@app.post("/admin/backup/restore")
def admin_backup_restore():
    if not session.get("is_admin"):
        return jsonify({"success": False, "error": "Non autorizzato"}), 401

    file_storage = request.files.get("backup")
    if not file_storage:
        return jsonify({"success": False, "error": "File backup mancante"}), 400

    mode = (request.form.get("mode") or "merge").strip().lower()
    if mode not in {"merge", "replace"}:
        return jsonify({"success": False, "error": "Modalità non valida"}), 400

    try:
        incoming_days, incoming_pauses = read_backup_zip(file_storage)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    pre_restore_name = f"_pre_restore_{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.zip"

    with write_lock:
        current_days = {}
        for fn in list_day_json_files():
            dstr = fn[:-5]
            current_days[fn] = normalize_day_payload(dstr, read_day(dstr))
        current_pauses = read_pauses()

        pre_restore_zip = build_zip_bytes(current_days, generated_by="auto-pre-restore", pauses_data=current_pauses)
        with open(os.path.join(PM_DATA_DIR, pre_restore_name), "wb") as f:
            f.write(pre_restore_zip.getbuffer())

        if mode == "replace":
            for fn in list_day_json_files():
                try:
                    os.remove(os.path.join(PM_DATA_DIR, fn))
                except Exception:
                    pass
            merged_days = incoming_days
            merged_pauses = {"paused_dates": normalize_paused_dates(incoming_pauses.get("paused_dates", []))}
        else:
            merged_days = dict(current_days)
            for fn, incoming in incoming_days.items():
                if fn not in merged_days:
                    merged_days[fn] = incoming
                    continue
                base = merged_days[fn]
                merged_map = {(e.get("name") or "").lower(): e for e in base.get("entries", [])}
                for e in incoming.get("entries", []):
                    merged_map[(e.get("name") or "").lower()] = e
                merged_days[fn] = {
                    "date": incoming["date"],
                    "entries": sorted(merged_map.values(), key=lambda x: (x.get("name") or "").lower()),
                    "updated_at": datetime.utcnow().isoformat(),
                }
            merged_pause_set = set(normalize_paused_dates(current_pauses.get("paused_dates", [])))
            merged_pause_set.update(normalize_paused_dates(incoming_pauses.get("paused_dates", [])))
            merged_pauses = {"paused_dates": sorted(merged_pause_set)}

        for fn, payload in merged_days.items():
            with open(os.path.join(PM_DATA_DIR, fn), "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        pause_payload = {
            "paused_dates": normalize_paused_dates(merged_pauses.get("paused_dates", [])),
            "updated_at": datetime.utcnow().isoformat(),
        }
        with open(pause_path(), "w", encoding="utf-8") as f:
            json.dump(pause_payload, f, ensure_ascii=False, indent=2)

    return jsonify({
        "success": True,
        "mode": mode,
        "imported_files": len(incoming_days),
        "paused_imported": len(normalize_paused_dates(incoming_pauses.get("paused_dates", []))),
        "pre_restore_backup": pre_restore_name,
    })


# ================== MAIN ==================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
