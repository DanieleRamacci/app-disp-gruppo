<?php
/**
 * Presenze Martedì — UNA SOLA PAGINA (login + scelte + riepilogo)
 * Autenticazione STATELESS con token HMAC + fallback cookie
 *
 * Shortcode: [presenze_martedi weeks="20" min_presence="4"]
 * Requisiti: WP 5+, PHP 7.4+
 *
 * STATI: presence (in presenza/biglietto), flexible, remote
 */

const PM_TITLE      = 'Presenze Martedì';
const PM_TIMEZONE   = 'Europe/Rome';
const PM_WEEKS_DEF  = 20;      // settimane visibili di default
const PM_MIN_P_DEF  = 4;       // soglia minima presenza (alert)
const PM_PASSCODE   = '1234';  // ⚠️ CAMBIA SUBITO
const PM_DATA_DIR   = 'presenze/data'; // path relativo dentro wp-uploads
const PM_TOKEN_TTL  = 2592000; // 30 giorni in secondi

add_action('init', function(){
  date_default_timezone_set(PM_TIMEZONE);
});

/* ----------------- COOKIE AUTH (fallback) ----------------- */
function pm_cookie_params(){
  $path   = defined('COOKIEPATH') && COOKIEPATH ? COOKIEPATH : '/';
  $domain = defined('COOKIE_DOMAIN') ? COOKIE_DOMAIN : '';
  $secure = function_exists('is_ssl') ? is_ssl() : (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off');
  return [$path, $domain, $secure];
}
function pm_set_user_cookie($name){
  [$path, $domain, $secure] = pm_cookie_params();
  $expires = time() + PM_TOKEN_TTL;
  if (PHP_VERSION_ID >= 70300) {
    setcookie('pm_name', $name, [
      'expires'  => $expires,
      'path'     => $path,
      'domain'   => $domain,
      'secure'   => $secure,
      'httponly' => true,
      'samesite' => 'Lax',
    ]);
  } else {
    setcookie('pm_name', $name, $expires, $path, $domain, $secure, true);
  }
  $_COOKIE['pm_name'] = $name; // disponibile subito
}
function pm_clear_user_cookie(){
  [$path, $domain, $secure] = pm_cookie_params();
  if (PHP_VERSION_ID >= 70300) {
    setcookie('pm_name', '', [
      'expires'  => time() - 3600,
      'path'     => $path,
      'domain'   => $domain,
      'secure'   => $secure,
      'httponly' => true,
      'samesite' => 'Lax',
    ]);
  } else {
    setcookie('pm_name', '', time()-3600, $path, $domain, $secure, true);
  }
  unset($_COOKIE['pm_name']);
}
function pm_current_user_from_cookie(){
  return !empty($_COOKIE['pm_name']) ? sanitize_text_field($_COOKIE['pm_name']) : null;
}

/* ----------------- TOKEN STATELESS ----------------- */
function pm_issue_token($name, $ttl=PM_TOKEN_TTL){
  $exp = time() + $ttl;
  $payload = $name.'|'.$exp;
  $sig = hash_hmac('sha256', $payload, PM_PASSCODE);
  return ['name'=>$name, 'exp'=>$exp, 'sig'=>$sig];
}
function pm_verify_token($name,$exp,$sig){
  if(!$name || !$exp || !$sig) return false;
  if(!is_numeric($exp) || $exp < time()) return false;
  $calc = hash_hmac('sha256', $name.'|'.$exp, PM_PASSCODE);
  return hash_equals($calc,$sig);
}
/** Rileva utente: prima cookie, altrimenti token HMAC (pm_name, pm_exp, pm_sig) */
function pm_auth_user(){
  $cookie = pm_current_user_from_cookie();
  if ($cookie) return $cookie;
  $name = isset($_REQUEST['pm_name']) ? pm_sanitize_name($_REQUEST['pm_name']) : '';
  $exp  = isset($_REQUEST['pm_exp'])  ? intval($_REQUEST['pm_exp']) : 0;
  $sig  = isset($_REQUEST['pm_sig'])  ? (string)$_REQUEST['pm_sig'] : '';
  if (pm_verify_token($name,$exp,$sig)) return $name;
  return null;
}

/* ----------------- UTIL ----------------- */
function pm_upload_paths(){
  $up   = wp_upload_dir(null, false);
  $base = trailingslashit($up['basedir']) . PM_DATA_DIR;
  if (!file_exists($base)) wp_mkdir_p($base);
  // Protezione JSON
  $ht = $base.'/.htaccess';
  if (!file_exists($ht)) {
    @file_put_contents($ht, <<<HT
<IfModule mod_authz_core.c>
  Require all denied
</IfModule>
<IfModule !mod_authz_core.c>
  Order allow,deny
  Deny from all
</IfModule>
HT);
  }
  return [$base];
}
function pm_day_path($date){ [$base] = pm_upload_paths(); return $base . '/' . $date . '.json'; }
function pm_is_tuesday($date){ $ts=strtotime($date); return $ts && (date('N',$ts)==='2'); }
function pm_require_tuesday($date){
  if (!preg_match('/^\d{4}-\d{2}-\d{2}$/',$date) || !pm_is_tuesday($date)) wp_send_json_error(['error'=>'Data non valida (martedì, YYYY-MM-DD)'],400);
  return $date;
}
function pm_next_tuesdays($weeks){
  $out=[]; $today=new DateTimeImmutable('today'); $dow=(int)$today->format('N');
  $offset=(2-$dow); if($offset<0)$offset+=7; $first=$today->modify("+$offset days");
  for($i=0;$i<$weeks;$i++) $out[]=$first->modify("+".($i*7)." days")->format('Y-m-d');
  return $out;
}
function pm_substr_safe($s,$a,$l){ return function_exists('mb_substr')?mb_substr($s,$a,$l):substr($s,$a,$l); }
function pm_sanitize_name($raw){ $name=trim(preg_replace('/\s+/', ' ', (string)$raw)); return pm_substr_safe($name,0,60); }
function pm_sanitize_status($s){
  $s=strtolower(trim((string)$s));
  return in_array($s,['presence','flexible','remote'],true)?$s:'';
}
function pm_read_day($date){
  $file=pm_day_path($date);
  if(!file_exists($file)) return ['date'=>$date,'entries'=>[],'updated_at'=>null];
  $data=json_decode(@file_get_contents($file),true);
  if(!is_array($data)) $data=['date'=>$date,'entries'=>[],'updated_at'=>null];
  if(!isset($data['entries'])||!is_array($data['entries'])) $data['entries']=[];
  return $data;
}
function pm_write_day($date,$entry){
  $file=pm_day_path($date);
  $data=file_exists($file)?json_decode(file_get_contents($file),true):['date'=>$date,'entries'=>[]];
  if(!is_array($data)) $data=['date'=>$date,'entries'=>[]];

  $name=$entry['name']; $new=[]; $found=false;
  foreach($data['entries'] as $e){
    if(strcasecmp($e['name']??'',$name)===0){ $new[]=$entry; $found=true; }
    else { $new[]=$e; }
  }
  if(!$found) $new[]=$entry;
  $data['entries']=array_values($new);
  $data['updated_at']=gmdate('c');

  $fp=fopen($file,'c+');
  if($fp){
    if(flock($fp,LOCK_EX)){
      ftruncate($fp,0);
      fwrite($fp,json_encode($data,JSON_UNESCAPED_UNICODE|JSON_PRETTY_PRINT));
      fflush($fp);
      flock($fp,LOCK_UN);
    }
    fclose($fp);
  } else {
    file_put_contents($file,json_encode($data,JSON_UNESCAPED_UNICODE|JSON_PRETTY_PRINT));
  }
  return $data;
}
function pm_find_status($entries,$name){
  foreach($entries as $e){ if(strcasecmp($e['name']??'',$name)===0) return $e['status']??null; }
  return null;
}

/* ----------------- AJAX ----------------- */
add_action('wp_ajax_pm_login','pm_login'); add_action('wp_ajax_nopriv_pm_login','pm_login');
function pm_login(){
  check_ajax_referer('pm_nonce','nonce');
  $name=pm_sanitize_name($_POST['name']??'');
  $pass=trim((string)($_POST['pass']??''));

  if(!$name||!$pass||!hash_equals(PM_PASSCODE,$pass)){
    wp_send_json_error(['error'=>'Credenziali non valide'],401);
  }

  // fallback cookie
  pm_set_user_cookie($name);

  // token stateless
  $token = pm_issue_token($name);
  wp_send_json_success(['name'=>$name,'token'=>$token]);
}

add_action('wp_ajax_pm_logout','pm_logout'); add_action('wp_ajax_nopriv_pm_logout','pm_logout');
function pm_logout(){
  check_ajax_referer('pm_nonce','nonce');
  pm_clear_user_cookie();
  wp_send_json_success();
}

add_action('wp_ajax_pm_list','pm_list'); add_action('wp_ajax_nopriv_pm_list','pm_list');
function pm_list(){
  check_ajax_referer('pm_nonce','nonce');
  $me = pm_auth_user();
  if(empty($me)) wp_send_json_error(['error'=>'Non autenticato'],401);

  $weeks=max(1,min(52,intval($_GET['weeks']??PM_WEEKS_DEF)));
  $dates=pm_next_tuesdays($weeks);
  $rows=[];
  foreach($dates as $d){
    $data=pm_read_day($d);
    $counts=['presence'=>0,'flexible'=>0,'remote'=>0];
    foreach($data['entries'] as $e){
      $st=$e['status']??'';
      if(isset($counts[$st])) $counts[$st]++;
    }
    $mine=pm_find_status($data['entries'],$me);
    $rows[]=['date'=>$d,'counts'=>$counts,'my'=>$mine];
  }
  wp_send_json_success(['days'=>$rows,'me'=>$me]);
}

add_action('wp_ajax_pm_save','pm_save'); add_action('wp_ajax_nopriv_pm_save','pm_save');
function pm_save(){
  check_ajax_referer('pm_nonce','nonce');
  $me = pm_auth_user();
  if(empty($me)) wp_send_json_error(['error'=>'Non autenticato'],401);

  $date=pm_require_tuesday((string)($_POST['date']??''));
  $status=pm_sanitize_status($_POST['status']??'');
  if(!$status) wp_send_json_error(['error'=>'Stato non valido'],400);

  $data=pm_write_day($date,['name'=>$me,'status'=>$status]);
  wp_send_json_success(['data'=>$data]);
}

/* Lista nomi per login */
add_action('wp_ajax_pm_names','pm_names'); add_action('wp_ajax_nopriv_pm_names','pm_names');
function pm_names(){
  check_ajax_referer('pm_nonce','nonce');
  [$base] = pm_upload_paths();
  $names = [];
  foreach (glob($base.'/*.json') as $f){
    $data = json_decode(@file_get_contents($f), true);
    if (!empty($data['entries']) && is_array($data['entries'])){
      foreach ($data['entries'] as $e){
        $n = isset($e['name']) ? trim((string)$e['name']) : '';
        if ($n !== '') $names[$n] = true;
      }
    }
  }
  wp_send_json_success(array_values(array_keys($names)));
}

/* Riepilogo completo (nomi per ciascun martedì) */
add_action('wp_ajax_pm_summary','pm_summary'); add_action('wp_ajax_nopriv_pm_summary','pm_summary');
function pm_summary(){
  check_ajax_referer('pm_nonce','nonce');
  // Se vuoi obbligare al login anche il riepilogo, abilita:
  // if(empty(pm_auth_user())) wp_send_json_error(['error'=>'Non autenticato'],401);

  $weeks=max(1,min(52,intval($_GET['weeks']??PM_WEEKS_DEF)));
  $dates=pm_next_tuesdays($weeks);
  $out=[];
  foreach($dates as $d){
    $data=pm_read_day($d);
    $lists=['presence'=>[], 'flexible'=>[], 'remote'=>[]];
    foreach($data['entries'] as $e){
      $n = isset($e['name']) ? trim((string)$e['name']) : '';
      $s = isset($e['status']) ? (string)$e['status'] : '';
      if ($n!=='' && isset($lists[$s])) $lists[$s][]=$n;
    }
    foreach($lists as $k=>$arr){ sort($arr, SORT_FLAG_CASE|SORT_STRING); $lists[$k]=$arr; }
    $out[]=[
      'date'=>$d,
      'lists'=>$lists,
      'counts'=>[
        'presence'=>count($lists['presence']),
        'flexible'=>count($lists['flexible']),
        'remote'=>count($lists['remote']),
      ]
    ];
  }
  wp_send_json_success(['days'=>$out]);
}

/* ----------------- SHORTCODE: UNICO ----------------- */
add_shortcode('presenze_martedi', function($atts){
  $atts  = shortcode_atts(['weeks'=>PM_WEEKS_DEF, 'min_presence'=>PM_MIN_P_DEF], $atts, 'presenze_martedi');
  $weeks = intval($atts['weeks']) ?: PM_WEEKS_DEF;
  $minP  = intval($atts['min_presence']) ?: PM_MIN_P_DEF;

  $nonce  = wp_create_nonce('pm_nonce');
  $ajax   = admin_url('admin-ajax.php');
  $me     = pm_current_user_from_cookie(); // solo per decidere la vista iniziale

  ob_start(); ?>
  <div id="pm-app" class="pm-wrap" data-weeks="<?php echo esc_attr($weeks); ?>" data-min="<?php echo esc_attr($minP); ?>">

    <!-- ===== MENU FISSO (sticky) ===== -->
    <div id="pmFixedBar" class="pm-fixedbar">
      <div class="pm-fixedbar-inner">
        <div class="pm-fixedbar-title"><?php echo esc_html(PM_TITLE); ?></div>
        <div class="pm-fixedbar-actions">
          <button class="pm-btn pm-go-choices" id="pmGoChoicesTop" style="<?php echo $me?'':'display:none' ?>">Scelte</button>
          <button class="pm-btn pm-go-summary" id="pmGoSummaryTop" style="<?php echo $me?'':'display:none' ?>">Riepilogo</button>
          <button class="pm-btn pm-logout" id="pmLogoutTop" style="<?php echo $me?'':'display:none' ?>">Esci</button>
        </div>
      </div>
    </div>

    <!-- Titolo + badge (facoltativo) -->
    <div class="pm-top">
      <h2><?php echo esc_html(PM_TITLE); ?></h2>
      <span id="pmBadge" class="pm-badge" style="<?php echo $me?'':'display:none' ?>"><?php echo $me?esc_html($me):''; ?></span>
    </div>

    <!-- Vista LOGIN -->
    <div id="pmLoginView" style="<?php echo $me?'display:none':'' ?>">
      <div class="pm-card">
        <div class="pm-login">
          <div id="pmNameChips" class="pm-chips" aria-label="Utenti già registrati"></div>

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

    <!-- Vista APP (scelte + riepilogo) -->
    <div id="pmAppView" style="<?php echo $me?'':'display:none' ?>">

      <!-- Anchor "Scelte" per lo scroll -->
      <div id="pmChoicesAnchor"></div>

      <!-- Barra mesi (SCELTE) -->
      <div class="pm-monthbar" id="pmMonthBarChoices"></div>

      <!-- Sezione scelta stati -->
      <div class="pm-card pm-muted">Stati: Presenza · Flessibile · Remoto</div>
      <div id="pmDays" class="pm-grid"></div>

      <!-- Anchor "Riepilogo" + bottone -->
      <div id="pmSummaryAnchor" style="margin-top:8px"></div>
      <div style="display:flex;justify-content:flex-end;margin:10px 0">
        <button id="pmGoSummary" class="pm-btn">Vai al riepilogo</button>
      </div>

      <!-- Riepilogo -->
      <div class="pm-card" id="pmSummaryCard" style="margin-top:14px">
        <div class="pm-row" style="justify-content:space-between;align-items:center;gap:8px">
          <h3 style="margin:0">Riepilogo (con conteggi per martedì)</h3>
          <div><button id="pmRefreshAll" class="pm-btn">Aggiorna</button></div>
        </div>

        <!-- Barra mesi (RIEPILOGO) -->
        <div class="pm-monthbar" id="pmMonthBarSummary" style="margin-top:8px"></div>

        <div id="pmSummary"></div>
      </div>
    </div>
  </div>

  <!-- ===== SPINNER OVERLAY ===== -->
  <div id="pmSpinner" class="pm-overlay" style="display:none" aria-live="polite" aria-busy="true">
    <div class="pm-loader" role="status" aria-label="Caricamento"></div>
    <div class="pm-loadtext" id="pmSpinText">Salvataggio in corso…</div>
  </div>

  <style>
    /* Tema & layout */
    .pm-wrap{--gap:12px;--r:14px;--txt:#111827;--mut:#6b7280;--card:#f7f9fc;--bd:#e5e7eb;--bg:#ffffff;--primary:#2563eb;--danger:#ef4444}
    .pm-wrap *{box-sizing:border-box}
    .pm-wrap{color:var(--txt)}
    .pm-top{display:flex;gap:8px;align-items:center;justify-content:space-between;margin:10px 0;flex-wrap:wrap}
    .pm-badge{background:#f3f4f6;border:1px solid var(--bd);padding:6px 10px;border-radius:999px;color:var(--txt)}
    .pm-btn{background:#ffffff;border:1px solid var(--bd);color:var(--txt);border-radius:12px;padding:10px 14px;cursor:pointer;transition:all .2s;font-weight:600}
    .pm-btn:hover{background:#f9fafb}
    .pm-card{background:var(--card);border:1px solid var(--bd);border-radius:14px;padding:14px;margin-bottom:12px}
    .pm-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:var(--gap)}

    .pm-pill{border-radius:999px;padding:12px 14px;border:1px solid var(--bd);background:#ffffff;color:var(--txt);font-size:15px;font-weight:600;transition:all .2s;flex:1;min-width:110px;text-align:center}
    .pm-pill:hover{background:#f3f4f6}
    .pm-pill.sel{background:var(--primary);border-color:var(--primary);color:#fff}

    .pm-row{display:flex;gap:8px;align-items:center;margin-bottom:8px;flex-wrap:wrap}
    .pm-name{font-weight:700}
    .pm-login{display:grid;gap:10px}
    .pm-badges{display:flex;gap:8px;flex-wrap:wrap}
    .pm-stat{background:#fff;border:1px solid var(--bd);border-radius:999px;padding:6px 10px;font-size:13px}
    input[type="text"], input[type="password"]{width:100%;background:#fff;border:1px solid var(--bd);border-radius:10px;padding:12px;font-size:16px}

    /* Chips nomi */
    .pm-chips{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:8px}
    .pm-chip{display:inline-flex;align-items:center;gap:6px;padding:6px 10px;border-radius:999px;background:#fff;border:1px solid var(--bd);cursor:pointer;font-size:13px;user-select:none}
    .pm-chip:hover{background:#f9fafb}

    /* Barre mesi */
    .pm-monthbar{position:sticky;top:48px; /* sotto la fixedbar */ z-index:8;background:var(--bg);padding:8px;border-bottom:1px solid var(--bd);display:flex;gap:8px;overflow:auto}
    .pm-month{white-space:nowrap;border:1px solid var(--bd);background:#fff;border-radius:999px;padding:6px 10px;cursor:pointer}
    .pm-month.active{background:#e8effe;border-color:#cfe0ff}

    /* MENU FISSO IN ALTO */
    .pm-fixedbar{position:sticky;top:0;z-index:999;background:var(--bg);border-bottom:1px solid var(--bd)}
    .pm-fixedbar-inner{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:8px 0}
    .pm-fixedbar-title{font-weight:700;font-size:16px}
    .pm-fixedbar-actions{display:flex;gap:8px;flex-wrap:wrap}

    /* Overlay spinner */
    .pm-overlay{position:fixed;inset:0;background:rgba(255,255,255,.7);display:flex;flex-direction:column;gap:10px;align-items:center;justify-content:center;padding:20px;z-index:9999}
    .pm-loader{width:42px;height:42px;border-radius:50%;border:4px solid #e5e7eb;border-top-color:var(--primary);animation:pm-spin .9s linear infinite}
    .pm-loadtext{font-weight:600;color:#111827}
    @keyframes pm-spin { to { transform: rotate(360deg); } }
  </style>

  <script>
  (function(){
    const ajax   = <?php echo json_encode($ajax); ?>;
    const nonce  = <?php echo json_encode($nonce); ?>;
    const weeks  = parseInt(document.getElementById('pm-app').dataset.weeks || '<?php echo (int)$weeks; ?>',10);
    const minP   = parseInt(document.getElementById('pm-app').dataset.min   || '<?php echo (int)$minP; ?>',10);

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

    /* ===== Token helpers ===== */
    function getToken(){
      try{
        const n = localStorage.getItem('pm_name');
        const e = localStorage.getItem('pm_exp');
        const s = localStorage.getItem('pm_sig');
        if(n && e && s) return {pm_name:n, pm_exp:e, pm_sig:s};
      }catch(_){}
      return null;
    }
    function setToken(t){
      try{
        localStorage.setItem('pm_name', t.name);
        localStorage.setItem('pm_exp',  t.exp);
        localStorage.setItem('pm_sig',  t.sig);
      }catch(_){}
    }
    function clearToken(){
      try{
        localStorage.removeItem('pm_name');
        localStorage.removeItem('pm_exp');
        localStorage.removeItem('pm_sig');
      }catch(_){}
    }

    /* ===== Spinner helpers ===== */
    let _spinCount = 0;
    function showSpinner(text){
      try{
        _spinCount++;
        const s=q('#pmSpinner'); const t=q('#pmSpinText');
        if(t && text) t.textContent = text;
        if(s) s.style.display='flex';
      }catch(_){}
    }
    function hideSpinner(){
      try{
        _spinCount = Math.max(0,_spinCount-1);
        if(_spinCount===0){ const s=q('#pmSpinner'); if(s) s.style.display='none'; }
      }catch(_){}
    }

    /* ===== AJAX helpers ===== */
    async function post(action, body){
      const fd = new FormData();
      fd.append('action', action);
      fd.append('nonce', nonce);
      Object.entries(body||{}).forEach(([k,v])=>fd.append(k, v));
      const tok = getToken();
      if (tok){
        fd.append('pm_name', tok.pm_name);
        fd.append('pm_exp',  tok.pm_exp);
        fd.append('pm_sig',  tok.pm_sig);
      }
      const res = await fetch(ajax, { method:'POST', body: fd, credentials:'same-origin' });
      const j = await res.json();
      if(!j?.success) throw new Error(j?.data?.error || 'Errore');
      return j.data;
    }
    async function getJSON(action, params={}){
      const url = new URL(ajax);
      url.searchParams.set('action', action);
      url.searchParams.set('nonce',  nonce);
      Object.entries(params).forEach(([k,v])=>url.searchParams.set(k, v));
      const tok = getToken();
      if (tok){
        url.searchParams.set('pm_name', tok.pm_name);
        url.searchParams.set('pm_exp',  tok.pm_exp);
        url.searchParams.set('pm_sig',  tok.pm_sig);
      }
      const res = await fetch(url, { credentials:'same-origin' });
      const j = await res.json();
      if(!j?.success) throw new Error(j?.data?.error || 'Errore');
      return j.data;
    }

    /* ===== Barre mesi (doppie) ===== */
    function monthKey(dateStr){ const d=new Date(dateStr); return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0'); }
    function monthLabel(dateStr){ return new Date(dateStr).toLocaleDateString('it-IT',{month:'long',year:'numeric'}); }
    function buildMonthBar(dates, barId, target){
      const bar = document.getElementById(barId); if(!bar) return;
      bar.innerHTML='';
      const seen = new Set();
      dates.forEach(d=>{
        const key=monthKey(d);
        if(seen.has(key)) return; seen.add(key);
        const btn=document.createElement('button');
        btn.className='pm-month'; btn.dataset.month=key; btn.textContent=monthLabel(d);
        btn.addEventListener('click', ()=> {
          if(target==='summary'){
            const elMonth = document.getElementById('m-'+key);
            if(elMonth) elMonth.scrollIntoView({behavior:'smooth', block:'start'});
          }else{
            const elDay = document.querySelector('#pmDays [data-month="'+key+'"]');
            if(elDay) elDay.scrollIntoView({behavior:'smooth', block:'start'});
          }
        });
        bar.appendChild(btn);
      });
    }

    /* ===== Lista nomi ===== */
    function renderNames(names){
      const cont = q('#pmNameChips');
      const dl   = q('#pmNameList');
      if (!cont || !dl) return;

      const uniq = Array.from(new Set((names||[]).map(n=> (n||'').trim()))).filter(Boolean);
      uniq.sort((a,b)=> a.localeCompare(b, 'it', {sensitivity:'base'}));

      cont.innerHTML = '';
      uniq.forEach(n=>{
        const chip = el('button',{type:'button', class:'pm-chip', onclick:()=>{
          const inp=q('#pmName'); if(inp){ inp.value=n; inp.focus(); }
          try{ localStorage.setItem('pm_name', n); }catch(_){}
        }}, n);
        cont.appendChild(chip);
      });

      dl.innerHTML = '';
      uniq.forEach(n=> dl.appendChild(el('option',{}, n)));
    }
    async function loadNames(){
      try{ const data = await getJSON('pm_names'); renderNames(data); }
      catch(e){ console.warn('Nomi non caricati', e); }
    }

    /* ===== Cards giorni (scelte) ===== */
    function cardDay(d){
      const {date, counts, my} = d;
      const c = el('div',{class:'pm-card', id:'day-'+date, 'data-month':monthKey(date)});
      const head = el('div',{class:'pm-row', style:'justify-content:space-between;align-items:center'},[
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
      const actions = el('div',{class:'pm-row'},[
        el('button',{class:'pm-pill'+(my==='presence'?' sel':''), onclick:()=>save(date,'presence')},'Presenza'),
        el('button',{class:'pm-pill'+(my==='flexible'?' sel':''), onclick:()=>save(date,'flexible')},'Flessibile'),
        el('button',{class:'pm-pill'+(my==='remote'?' sel':''),   onclick:()=>save(date,'remote')},  'Remoto'),
      ]);
      const mine = el('div',{class:'pm-muted', style:'margin-top:8px'},'Tuo stato: ' + (my?({'presence':'Presenza','flexible':'Flessibile','remote':'Remoto'})[my]:'—'));
      c.append(head,actions,mine);
      return c;
    }

    async function refreshDays(){
      const cont = q('#pmDays'); if(!cont) return;
      cont.textContent='Caricamento...';
      try{
        const data = await getJSON('pm_list', {weeks});
        cont.textContent='';
        const dates = data.days.map(x=>x.date);
        buildMonthBar(dates, 'pmMonthBarChoices', 'choices');
        data.days.forEach(d=> cont.appendChild(cardDay(d)));
      }catch(e){ cont.textContent=e.message || 'Errore'; }
    }

    async function save(date, status){
      const btns = document.querySelectorAll('.pm-pill'); btns.forEach(b=>b.disabled=true);
      showSpinner('Salvataggio in corso…');
      try{
        await post('pm_save', { date, status });
        await refreshDays();
        await refreshSummary();
      }catch(e){
        alert(e.message || 'Errore salvataggio');
      }finally{
        btns.forEach(b=>b.disabled=false);
        hideSpinner();
      }
    }

    /* ===== Riepilogo ===== */
    function listCol(title, arr){
      const col = el('div',{class:'pm-col'});
      col.append(el('h4',{}, title));
      if(arr && arr.length){
        const ul=el('ul',{class:'pm-list'});
        arr.forEach(n=> ul.append(el('li',{}, n)));
        col.append(ul);
      } else {
        col.append(el('div',{class:'pm-muted'}, 'Nessuno'));
      }
      return col;
    }
    function summaryRow(day){
      const d=day.date, counts=day.counts, lists=day.lists;
      const alert = counts.presence < minP; // sotto soglia SOLO sulla presenza
      const wrap = el('div',{class:'pm-rowcard', id:'sum-'+d, 'data-month':monthKey(d)});
      const top = el('div',{class:'pm-row', style:'justify-content:space-between;align-items:center'},[
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
      const cols = el('div',{class:'pm-columns', style:'margin-top:10px'},[
        listCol('Presenza', lists.presence),
        listCol('Remoto',   lists.remote),
        listCol('Flessibile', lists.flexible)
      ]);
      wrap.append(top, cols);
      return wrap;
    }

    async function refreshSummary(){
      const cont = q('#pmSummary'); if(!cont) return;
      cont.textContent='Caricamento...';
      try{
        const data = await getJSON('pm_summary', {weeks});
        cont.textContent='';
        const dates = data.days.map(x=>x.date);

        // barra mesi del riepilogo
        buildMonthBar(dates, 'pmMonthBarSummary', 'summary');

        // blocchi per mese con ancora #m-YYYY-MM
        let currentMonth='';
        data.days.forEach(day=>{
          const mk = monthKey(day.date);
          if (mk !== currentMonth){
            currentMonth = mk;
            cont.append(el('h3',{id:'m-'+mk, style:'margin:14px 0 8px 0'}, new Date(day.date).toLocaleDateString('it-IT',{month:'long',year:'numeric'})));
          }
          cont.append(summaryRow(day));
        });
      }catch(e){ cont.textContent=e.message || 'Errore'; }
    }

    /* ===== Eventi ===== */

    // Login
    const btnLogin = q('#pmLogin');
    if (btnLogin){
      btnLogin.addEventListener('click', async ()=>{
        const name=(q('#pmName')?.value||'').trim();
        const pass=(q('#pmPass')?.value||'').trim();
        const msg = q('#pmMsg');
        if(!name||!pass){ msg.textContent='Inserisci nome e passcode'; return; }
        msg.textContent='Accesso...';
        try{
          const data = await post('pm_login',{name,pass});
          if (data?.token){ setToken(data.token); }
          showApp(name);
          await refreshDays();
          await refreshSummary();
        }catch(e){
          msg.textContent=e.message || 'Errore di accesso';
          alert(e.message || 'Errore di accesso');
        }
      });
    }

    // Logout (anche da mobile)
    function doLogout(){
      showSpinner('Uscita in corso…');
      (async()=>{
        try{ await post('pm_logout',{}); }catch(_){}
        clearToken();
        showLogin();
      })().finally(hideSpinner);
    }
    const btnLogoutTop = q('#pmLogoutTop');
    if (btnLogoutTop){ btnLogoutTop.addEventListener('click', doLogout, {passive:true}); }

    // Pulsanti menu fisso
    const goChoicesTop = q('#pmGoChoicesTop');
    if (goChoicesTop){ goChoicesTop.addEventListener('click', ()=>{ const a = q('#pmChoicesAnchor')||q('#pmDays'); if(a) a.scrollIntoView({behavior:'smooth', block:'start'}); }); }
    const goSummaryTop = q('#pmGoSummaryTop');
    if (goSummaryTop){ goSummaryTop.addEventListener('click', ()=>{ const a = q('#pmSummaryAnchor')||q('#pmSummaryCard'); if(a) a.scrollIntoView({behavior:'smooth', block:'start'}); }); }

    // Bottone "Vai al riepilogo"
    const btnGoSummary = q('#pmGoSummary');
    if (btnGoSummary){
      btnGoSummary.addEventListener('click', ()=>{
        const anchor = q('#pmSummaryAnchor') || q('#pmSummaryCard');
        if(anchor) anchor.scrollIntoView({behavior:'smooth', block:'start'});
      });
    }

    // Refresh
    const btnRefreshAll = q('#pmRefreshAll');
    if (btnRefreshAll){
      btnRefreshAll.addEventListener('click', async ()=>{
        showSpinner('Aggiornamento…');
        try{ await refreshDays(); await refreshSummary(); }
        finally{ hideSpinner(); }
      });
    }

    // Prefill nome se salvato (non influisce sull'autenticazione)
    try{
      const storedName = localStorage.getItem('pm_name');
      if (storedName && q('#pmName')) q('#pmName').value = storedName;
    }catch(_){}

    // Toggle viste
    function showApp(name){
      const login = q('#pmLoginView');
      const app   = q('#pmAppView');
      const badge = q('#pmBadge');
      // mostra i bottoni nel menu fisso
      ['#pmGoChoicesTop','#pmGoSummaryTop','#pmLogoutTop'].forEach(sel=>{ const b=q(sel); if(b) b.style.display=''; });

      if(login) login.style.display='none';
      if(app)   app.style.display='';
      if(badge){ badge.textContent = name || (getToken()?.pm_name||''); badge.style.display=''; }
    }
    function showLogin(){
      const login = q('#pmLoginView');
      const app   = q('#pmAppView');
      const badge = q('#pmBadge');
      // nascondi i bottoni nel menu fisso
      ['#pmGoChoicesTop','#pmGoSummaryTop','#pmLogoutTop'].forEach(sel=>{ const b=q(sel); if(b) b.style.display='none'; });

      if(app)   app.style.display='none';
      if(login) login.style.display='';
      if(badge) badge.style.display='none';
    }

    // Inizializzazione
    if (q('#pmNameChips')) loadNames();
    (async ()=>{
      const tok = getToken();
      if (tok){
        try{
          await getJSON('pm_list', {weeks}); // valida token
          showApp(tok.pm_name);
          await refreshDays();
          await refreshSummary();
          return;
        }catch(_){}
      }
      // Se vista app già visibile (cookie), carica i dati
      if (q('#pmAppView') && getComputedStyle(q('#pmAppView')).display!=='none'){
        await refreshDays(); await refreshSummary();
      }
    })();
  })();
  </script>
  <?php
  return ob_get_clean();
});
