# server.py
from flask import Flask, request, jsonify, session, render_template_string, redirect
import os, json, threading
from datetime import datetime, date, timedelta

# ================== CONFIG ==================
SECRET_KEY     = "cambia-questa-chiave"   # cambia in produzione
PM_PASSCODE    = "1234"                   # ⚠️ cambia subito
PM_DATA_DIR    = "data"                   # dove salvare i JSON
PM_WEEKS_DEF   = 20                       # settimane visibili di default
PM_MIN_P_DEF   = 4                        # soglia minima "presence" per alert
TZ             = "Europe/Rome"            # usata solo per label; le date sono YYYY-MM-DD

app = Flask(__name__)
app.secret_key = SECRET_KEY
os.makedirs(PM_DATA_DIR, exist_ok=True)

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
    # prossimo martedì (o oggi se è martedì)
    offset = (1 - today.weekday()) % 7
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

    # scrittura protetta
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
    # una sola pagina: login + scelte + riepilogo (come WP)
    user = session.get("user")
    return render_template_string(PAGE_HTML, user=user, weeks=PM_WEEKS_DEF, min_presence=PM_MIN_P_DEF, title="Presenze Martedì")

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
    # lista nomi già presenti (pubblica, come WP)
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
    # riepilogo (pubblico come WP; se vuoi richiedere login togli commento sotto)
    # if not session.get("user"): return jsonify({"success": False, "error":"Non autenticato"}), 401
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

# ================== UI HTML (login + scelte + riepilogo) ==================
PAGE_HTML = r"""
<!doctype html>
<html lang="it">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>{{ title }}</title>
  <style>
    :root{--gap:12px;--r:14px;--txt:#111827;--mut:#6b7280;--card:#f7f9fc;--bd:#e5e7eb;--bg:#ffffff;--primary:#2563eb;--danger:#ef4444}
    *{box-sizing:border-box}
    body{margin:0;font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Arial;color:var(--txt);background:#fff}
    .container{max-width:980px;margin:0 auto;padding:12px}
    .pm-btn{background:#fff;border:1px solid var(--bd);color:var(--txt);border-radius:12px;padding:10px 14px;cursor:pointer;transition:all .2s;font-weight:600}
    .pm-btn:hover{background:#f9fafb}
    .pm-card{background:var(--card);border:1px solid var(--bd);border-radius:14px;padding:14px;margin-bottom:12px}
    .pm-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:var(--gap)}
    .pm-row{display:flex;gap:8px;align-items:center;margin-bottom:8px;flex-wrap:wrap}
    .pm-muted{color:var(--mut)}
    .pm-name{font-weight:700}
    .pm-badge{background:#f3f4f6;border:1px solid var(--bd);padding:6px 10px;border-radius:999px;color:var(--txt)}
    .pm-stat{background:#fff;border:1px solid var(--bd);border-radius:999px;padding:6px 10px;font-size:13px}
    .pm-pill{border-radius:999px;padding:12px 14px;border:1px solid var(--bd);background:#fff;color:var(--txt);font-size:15px;font-weight:600;transition:all .2s;flex:1;min-width:110px;text-align:center}
    .pm-pill:hover{background:#f3f4f6}
    .pm-pill.sel{background:var(--primary);border-color:var(--primary);color:#fff}
    input[type="text"], input[type="password"]{width:100%;background:#fff;border:1px solid var(--bd);border-radius:10px;padding:12px;font-size:16px}
    .pm-chips{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:8px}
    .pm-chip{display:inline-flex;align-items:center;gap:6px;padding:6px 10px;border-radius:999px;background:#fff;border:1px solid var(--bd);cursor:pointer;font-size:13px;user-select:none}
    .pm-chip:hover{background:#f9fafb}

    /* menu fisso */
    .pm-fixedbar{position:sticky;top:0;z-index:999;background:#fff;border-bottom:1px solid var(--bd)}
    .pm-fixedbar-inner{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:8px 12px}
    .pm-fixedbar-title{font-weight:700}

    /* monthbar */
    .pm-monthbar{position:sticky;top:48px;z-index:8;background:#fff;padding:8px;border-bottom:1px solid var(--bd);display:flex;gap:8px;overflow:auto}
    .pm-month{white-space:nowrap;border:1px solid var(--bd);background:#fff;border-radius:999px;padding:6px 10px;cursor:pointer}
    .pm-month.active{background:#e8effe;border-color:#cfe0ff}

    /* summary columns */
    .pm-rowcard{background:#fff;border:1px solid var(--bd);border-radius:12px;padding:12px}
    .pm-dot{display:inline-block;width:10px;height:10px;border-radius:999px;background:var(--danger);margin-right:6px;vertical-align:baseline}
    .pm-columns{display:grid;grid-template-columns:1fr;gap:10px}
    @media (min-width:720px){ .pm-columns{grid-template-columns:repeat(3,1fr);} }
    .pm-col h4{margin:0 0 6px 0;font-size:14px;color:#374151}
    .pm-list{margin:0;padding-left:16px}
    .pm-list li{margin:2px 0}

    /* overlay spinner */
    .pm-overlay{position:fixed;inset:0;background:rgba(255,255,255,.7);display:none;flex-direction:column;gap:10px;align-items:center;justify-content:center;padding:20px;z-index:9999}
    .pm-loader{width:42px;height:42px;border-radius:50%;border:4px solid #e5e7eb;border-top-color:var(--primary);animation:pm-spin .9s linear infinite}
    .pm-loadtext{font-weight:600;color:#111827}
    @keyframes pm-spin { to { transform: rotate(360deg); } }
  </style>
</head>
<body>
  <div class="pm-fixedbar">
    <div class="pm-fixedbar-inner container">
      <div class="pm-fixedbar-title">Presenze Martedì</div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <button class="pm-btn" id="pmGoChoicesTop" style="{{ '' if user else 'display:none' }}">Scelte</button>
        <button class="pm-btn" id="pmGoSummaryTop" style="{{ '' if user else 'display:none' }}">Riepilogo</button>
        <button class="pm-btn" id="pmLogoutTop" style="{{ '' if user else 'display:none' }}">Esci</button>
      </div>
    </div>
  </div>

  <div class="container">
    <!-- header/badge -->
    <div class="pm-row" style="justify-content:space-between">
      <h2>Presenze Martedì</h2>
      <span id="pmBadge" class="pm-badge" style="{{ '' if user else 'display:none' }}">{{ user or '' }}</span>
    </div>

    <!-- LOGIN -->
    <div id="pmLoginView" style="{{ 'display:none' if user else '' }}">
      <div class="pm-card">
        <div class="pm-chips" id="pmNameChips"></div>
        <div style="display:grid;gap:10px">
          <label>Nome
            <input id="pmName" list="pmNameList" maxlength="60" placeholder="Seleziona o scrivi il tuo nome" type="text">
            <datalist id="pmNameList"></datalist>
          </label>
          <label>Passcode di gruppo
            <input id="pmPass" type="password" placeholder="Inserisci passcode">
          </label>
          <button id="pmLogin" class="pm-btn">Entra</button>
          <div id="pmMsg" class="pm-muted"></div>
        </div>
      </div>
    </div>

    <!-- APP -->
    <div id="pmAppView" style="{{ '' if user else 'display:none' }}">
      <!-- anchor Scelte -->
      <div id="pmChoicesAnchor"></div>
      <!-- monthbar scelte -->
      <div class="pm-monthbar" id="pmMonthBarChoices"></div>

      <div class="pm-card pm-muted">Stati: Presenza · Flessibile · Remoto</div>
      <div id="pmDays" class="pm-grid"></div>

      <!-- anchor Riepilogo -->
      <div id="pmSummaryAnchor" style="margin-top:8px"></div>
      <div style="display:flex;justify-content:flex-end;margin:10px 0">
        <button id="pmGoSummary" class="pm-btn">Vai al riepilogo</button>
      </div>

      <div class="pm-card" id="pmSummaryCard" style="margin-top:14px">
        <div class="pm-row" style="justify-content:space-between;align-items:center;gap:8px">
          <h3 style="margin:0">Riepilogo (con conteggi per martedì)</h3>
          <div><button id="pmRefreshAll" class="pm-btn">Aggiorna</button></div>
        </div>
        <!-- monthbar riepilogo -->
        <div class="pm-monthbar" id="pmMonthBarSummary" style="margin-top:8px"></div>
        <div id="pmSummary"></div>
      </div>
    </div>
  </div>

  <!-- overlay spinner -->
  <div id="pmSpinner" class="pm-overlay" aria-live="polite" aria-busy="true">
    <div class="pm-loader" role="status" aria-label="Caricamento"></div>
    <div class="pm-loadtext" id="pmSpinText">Salvataggio in corso…</div>
  </div>

  <script>
  (function(){
    const weeks = {{ weeks|int }};
    const minP  = {{ min_presence|int }};

    function q(s, r=document){ return r.querySelector(s); }
    function el(tag, attrs={}, children=[]){
      const e=document.createElement(tag);
      for(const k in attrs){
        if(k==='class') e.className=attrs[k];
        else if(k.startsWith('on')) e.addEventListener(k.substring(2), attrs[k]);
        else e.setAttribute(k, attrs[k]);
      }
      (Array.isArray(children)?children:[children]).forEach(c=>e.append(c?.nodeType?c:document.createTextNode(c||'')));
      return e;
    }

    // spinner
    let _spinCount=0;
    function showSpinner(text){
      _spinCount++;
      const s=q('#pmSpinner'); const t=q('#pmSpinText');
      if(t && text) t.textContent=text;
      if(s) s.style.display='flex';
    }
    function hideSpinner(){
      _spinCount=Math.max(0,_spinCount-1);
      if(_spinCount===0){ const s=q('#pmSpinner'); if(s) s.style.display='none'; }
    }

    // -------- API helpers
    async function api(url, method='GET', body=null){
      const opt={method};
      if(body instanceof FormData){ opt.body=body; }
      else if(body){ opt.headers={'Content-Type':'application/json'}; opt.body=JSON.stringify(body); }
      const r = await fetch(url, opt);
      const j = await r.json();
      if(!j?.success) throw new Error(j?.error||'Errore');
      return j;
    }

    // month helpers
    function monthKey(dateStr){ const d=new Date(dateStr); return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0'); }
    function monthLabel(dateStr){ return new Date(dateStr).toLocaleDateString('it-IT',{month:'long',year:'numeric'}); }

    function buildMonthBar(dates, id, target){
      const bar=q('#'+id); if(!bar) return;
      bar.innerHTML='';
      const seen=new Set();
      dates.forEach(d=>{
        const key=monthKey(d);
        if(seen.has(key)) return; seen.add(key);
        const b=el('button',{class:'pm-month', 'data-month':key}, monthLabel(d));
        b.addEventListener('click', ()=>{
          if(target==='summary'){
            const elMonth=q('#m-'+key); if(elMonth) elMonth.scrollIntoView({behavior:'smooth', block:'start'});
          }else{
            const elDay=document.querySelector('#pmDays [data-month="'+key+'"]');
            if(elDay) elDay.scrollIntoView({behavior:'smooth', block:'start'});
          }
        });
        bar.appendChild(b);
      });
    }

    // login chips + datalist
    function renderNames(names){
      const cont=q('#pmNameChips'); const dl=q('#pmNameList');
      if(!cont||!dl) return;
      const uniq=Array.from(new Set((names||[]).map(n=>(n||'').trim()))).filter(Boolean);
      uniq.sort((a,b)=> a.localeCompare(b,'it',{sensitivity:'base'}));
      cont.innerHTML=''; dl.innerHTML='';
      uniq.forEach(n=>{
        const chip=el('button',{type:'button', class:'pm-chip', onclick:()=>{
          const inp=q('#pmName'); if(inp){ inp.value=n; inp.focus(); }
          try{ localStorage.setItem('pm_name',n);}catch(_){}
        }}, n);
        cont.appendChild(chip);
        dl.appendChild(el('option',{},n));
      });
    }
    async function loadNames(){
      try{ const j=await api('/names'); renderNames(j.data); }catch(e){ console.warn('Nomi non caricati', e); }
    }

    // cards scelte
    function cardDay(d){
      const {date, counts, my}=d;
      const c=el('div',{class:'pm-card', id:'day-'+date, 'data-month':monthKey(date)});
      const head=el('div',{class:'pm-row', style:'justify-content:space-between;align-items:center'},[
        el('div',{},[
          el('div',{class:'pm-name'}, new Date(date).toLocaleDateString('it-IT',{weekday:'long',day:'2-digit',month:'long',year:'numeric'})),
          el('div',{class:'pm-muted'}, 'ISO: '+date)
        ]),
        el('div',{class:'pm-badges'},[
          el('span',{class:'pm-stat'}, `Presenza: ${counts.presence}`),
          el('span',{class:'pm-stat'}, `Flessibile: ${counts.flexible}`),
          el('span',{class:'pm-stat'}, `Remoto: ${counts.remote}`)
        ])
      ]);
      const actions=el('div',{class:'pm-row'},[
        el('button',{class:'pm-pill'+(my==='presence'?' sel':''), onclick:()=>save(date,'presence')},'Presenza'),
        el('button',{class:'pm-pill'+(my==='flexible'?' sel':''), onclick:()=>save(date,'flexible')},'Flessibile'),
        el('button',{class:'pm-pill'+(my==='remote'?' sel':''),   onclick:()=>save(date,'remote')},  'Remoto'),
      ]);
      const mine=el('div',{class:'pm-muted', style:'margin-top:8px'}, 'Tuo stato: '+(my?({'presence':'Presenza','flexible':'Flessibile','remote':'Remoto'})[my]:'—'));
      c.append(head,actions,mine);
      return c;
    }

    async function refreshDays(){
      const cont=q('#pmDays'); if(!cont) return;
      cont.textContent='Caricamento...';
      try{
        const j=await api('/list');
        cont.textContent='';
        const dates=j.days.map(x=>x.date);
        buildMonthBar(dates,'pmMonthBarChoices','choices');
        j.days.forEach(d=>cont.appendChild(cardDay(d)));
      }catch(e){ cont.textContent=e.message||'Errore'; }
    }

    async function save(date,status){
      const btns=document.querySelectorAll('.pm-pill'); btns.forEach(b=>b.disabled=true);
      showSpinner('Salvataggio in corso…');
      try{
        const fd=new FormData(); fd.append('date',date); fd.append('status',status);
        await api('/save','POST',fd);
        await refreshDays(); await refreshSummary();
      }catch(e){ alert(e.message||'Errore salvataggio'); }
      finally{ btns.forEach(b=>b.disabled=false); hideSpinner(); }
    }

    // riepilogo
    function listCol(title, arr){
      const col=el('div',{class:'pm-col'});
      col.append(el('h4',{},title));
      if(arr && arr.length){
        const ul=el('ul',{class:'pm-list'});
        arr.forEach(n=> ul.append(el('li',{}, n)));
        col.append(ul);
      }else{
        col.append(el('div',{class:'pm-muted'}, 'Nessuno'));
      }
      return col;
    }
    function summaryRow(day){
      const d=day.date, counts=day.counts, lists=day.lists;
      const alert = (counts.presence||0) < minP; // soglia SOLO sulla presenza
      const wrap=el('div',{class:'pm-rowcard', id:'sum-'+d, 'data-month':monthKey(d)});
      const top=el('div',{class:'pm-row', style:'justify-content:space-between;align-items:center'},[
        el('div',{},[
          el('strong',{}, new Date(d).toLocaleDateString('it-IT',{weekday:'long',day:'2-digit',month:'long',year:'numeric'})),
          el('span',{class:'pm-muted'}, ' ('+d+') '),
          alert ? el('span',{class:'pm-muted'}, [el('span',{class:'pm-dot'}), ' sotto soglia di '+minP]) : ''
        ]),
        el('div',{class:'pm-badges'},[
          el('span',{class:'pm-stat'}, `Presenza: ${counts.presence}`),
          el('span',{class:'pm-stat'}, `Flessibile: ${counts.flexible}`),
          el('span',{class:'pm-stat'}, `Remoto: ${counts.remote}`)
        ])
      ]);
      const cols=el('div',{class:'pm-columns', style:'margin-top:10px'},[
        listCol('Presenza', lists.presence),
        listCol('Remoto',   lists.remote),
        listCol('Flessibile', lists.flexible)
      ]);
      wrap.append(top,cols);
      return wrap;
    }

    async function refreshSummary(){
      const cont=q('#pmSummary'); if(!cont) return;
      cont.textContent='Caricamento...';
      try{
        const j=await api('/summary');
        cont.textContent='';
        const dates=j.days.map(x=>x.date);
        buildMonthBar(dates,'pmMonthBarSummary','summary');

        let currentMonth='';
        j.days.forEach(day=>{
          const mk=monthKey(day.date);
          if(mk!==currentMonth){
            currentMonth=mk;
            cont.append(el('h3',{id:'m-'+mk, style:'margin:14px 0 8px 0'}, new Date(day.date).toLocaleDateString('it-IT',{month:'long',year:'numeric'})));
          }
          cont.append(summaryRow(day));
        });
      }catch(e){ cont.textContent=e.message||'Errore'; }
    }

    // eventi UI
    const btnLogin=q('#pmLogin');
    if(btnLogin){
      btnLogin.addEventListener('click', async ()=>{
        const name=(q('#pmName')?.value||'').trim();
        const pass=(q('#pmPass')?.value||'').trim();
        const msg=q('#pmMsg');
        if(!name||!pass){ msg.textContent='Inserisci nome e passcode'; return; }
        msg.textContent='Accesso...';
        const fd=new FormData(); fd.append('name',name); fd.append('pass',pass);
        try{
          const j=await api('/login','POST',fd);
          // mostra app
          ['#pmLoginView'].forEach(s=>{ const n=q(s); if(n) n.style.display='none'; });
          ['#pmAppView','#pmGoChoicesTop','#pmGoSummaryTop','#pmLogoutTop','#pmBadge'].forEach(s=>{ const n=q(s); if(n) n.style.display=''; });
          const badge=q('#pmBadge'); if(badge) badge.textContent=name;
          await refreshDays(); await refreshSummary();
        }catch(e){
          msg.textContent=e.message||'Errore di accesso';
          alert(e.message||'Errore di accesso');
        }
      });
    }

    function doLogout(){
      showSpinner('Uscita in corso…');
      (async()=>{
        try{ await api('/logout','POST'); }catch(_){}
        // torna a login
        ['#pmAppView','#pmGoChoicesTop','#pmGoSummaryTop','#pmLogoutTop','#pmBadge'].forEach(s=>{ const n=q(s); if(n) n.style.display='none'; });
        const login=q('#pmLoginView'); if(login) login.style.display='';
      })().finally(hideSpinner);
    }
    const btnLogoutTop=q('#pmLogoutTop'); if(btnLogoutTop){ btnLogoutTop.addEventListener('click', doLogout); }

    const btnGoSummary=q('#pmGoSummary'); if(btnGoSummary){
      btnGoSummary.addEventListener('click', ()=>{
        const a=q('#pmSummaryAnchor')||q('#pmSummaryCard'); if(a) a.scrollIntoView({behavior:'smooth', block:'start'});
      });
    }
    const goChoicesTop=q('#pmGoChoicesTop'); if(goChoicesTop){
      goChoicesTop.addEventListener('click', ()=>{ const a=q('#pmChoicesAnchor')||q('#pmDays'); if(a) a.scrollIntoView({behavior:'smooth', block:'start'}); });
    }
    const goSummaryTop=q('#pmGoSummaryTop'); if(goSummaryTop){
      goSummaryTop.addEventListener('click', ()=>{ const a=q('#pmSummaryAnchor')||q('#pmSummaryCard'); if(a) a.scrollIntoView({behavior:'smooth', block:'start'}); });
    }

    const btnRefreshAll=q('#pmRefreshAll'); if(btnRefreshAll){
      btnRefreshAll.addEventListener('click', async ()=>{
        showSpinner('Aggiornamento…');
        try{ await refreshDays(); await refreshSummary(); }
        finally{ hideSpinner(); }
      });
    }

    // prefill nome (solo comodità, non login)
    try{ const stored=localStorage.getItem('pm_name'); if(stored && q('#pmName')) q('#pmName').value=stored; }catch(_){}

    // init
    if(q('#pmNameChips')) loadNames();
    if({{ 'true' if user else 'false' }}){
      refreshDays(); refreshSummary();
    }
  })();
  </script>
</body>
</html>
"""

# ================== MAIN ==================
if __name__ == "__main__":
    # Avvio in produzione / container
    app.run(host="0.0.0.0", port=5000, debug=False)
