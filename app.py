"""Diaslog Spy — веб-дашборд на localhost.

  python app.py

Поднимает локальную страницу http://localhost:8000 :
  * лента пойманного (удалённые / изменённые / одноразовые) по всем аккаунтам,
  * статус и Старт/Стоп каждого аккаунта,
  * фильтры, поиск, счётчики, превью медиа.

Вход в аккаунт (номер/код) выполняется ОДИН раз через терминал: python main.py [profile].
Веб коды не запрашивает — это безопасно.
"""
import os
import sys
import json
import hmac
import hashlib
import asyncio
import logging
import threading
import mimetypes
from urllib.parse import urlparse, parse_qs, parse_qsl, unquote
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import profiles
import store
from store import Store
from capturer import Capturer

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
log = logging.getLogger("diaslog.app")

# В Docker слушаем 0.0.0.0 (порт наружу пробрасывается только на 127.0.0.1 хоста).
HOST = os.getenv("DIASLOG_HOST", "127.0.0.1")
PORT = int(os.getenv("DIASLOG_PORT") or os.getenv("PORT") or "8000")
# Пароль на дашборд (для обычного браузера). В Telegram Mini App вместо пароля
# используется подпись initData — каждый видит только свой аккаунт.
PASSWORD = os.getenv("DIASLOG_PASSWORD", "")

PROFILES = {}     # name -> Profile
CAPTURERS = {}    # name -> Capturer
LOOP = None       # главный asyncio-цикл


# ---------------- веб-страница ----------------
PAGE = r"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DIASLOG · Intercept Console</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Chakra+Petch:wght@500;600;700&family=Space+Mono:wght@400;700&family=Manrope:wght@400;500;600;700&display=swap" rel="stylesheet">
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
  :root{
    --ink:#070a0e; --bg:#0a0e14; --panel:#10151d; --panel2:#141b25;
    --line:#1f2731; --line2:#2a3542;
    --txt:#e9eef5; --mut:#7b8798; --dim:#586374;
    --cyan:#2fe3c7; --cyan-d:#0f8f7e;
    --red:#ff495c; --amber:#ffb22e; --violet:#b06bff;
    --glow:0 0 0 1px var(--line),0 18px 40px -22px #000;
    --mono:'Space Mono',ui-monospace,monospace;
    --disp:'Chakra Petch',sans-serif;
    --body:'Manrope',sans-serif;
  }
  *{box-sizing:border-box}
  html,body{margin:0}
  body{
    background:var(--bg); color:var(--txt); font-family:var(--body);
    font-size:15px; line-height:1.5; min-height:100vh; -webkit-font-smoothing:antialiased;
    background-image:
      radial-gradient(900px 500px at 88% -8%, rgba(47,227,199,.10), transparent 60%),
      radial-gradient(800px 480px at 8% 0%, rgba(176,107,255,.08), transparent 55%),
      linear-gradient(var(--bg),var(--bg));
  }
  /* сетка + зерно + виньетка */
  body::before{content:"";position:fixed;inset:0;z-index:0;pointer-events:none;
    background-image:linear-gradient(rgba(255,255,255,.022) 1px,transparent 1px),
                     linear-gradient(90deg,rgba(255,255,255,.022) 1px,transparent 1px);
    background-size:46px 46px; mask-image:radial-gradient(120% 90% at 50% 0%,#000,transparent 78%);}
  body::after{content:"";position:fixed;inset:0;z-index:0;pointer-events:none;opacity:.5;
    background:radial-gradient(120% 120% at 50% 40%,transparent 60%,rgba(0,0,0,.55));}
  .grain{position:fixed;inset:-50%;z-index:0;pointer-events:none;opacity:.035;
    background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='160' height='160'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='2'/></filter><rect width='100%25' height='100%25' filter='url(%23n)'/></svg>");}
  .wrap{position:relative;z-index:1;max-width:1080px;margin:0 auto;padding:0 22px 90px}

  /* ---------- хедер ---------- */
  header{position:sticky;top:0;z-index:20;margin:0 -22px;padding:18px 22px;
    background:linear-gradient(180deg,rgba(10,14,20,.92),rgba(10,14,20,.72));
    backdrop-filter:blur(14px);border-bottom:1px solid var(--line);overflow:hidden}
  header::after{content:"";position:absolute;left:0;right:0;bottom:0;height:1px;
    background:linear-gradient(90deg,transparent,var(--cyan),transparent);opacity:.5;
    animation:scan 6s linear infinite}
  @keyframes scan{0%{transform:translateX(-60%)}100%{transform:translateX(60%)}}
  .brand{display:flex;align-items:center;gap:13px}
  .sig{width:14px;height:14px;border-radius:50%;background:var(--cyan);
    box-shadow:0 0 0 4px rgba(47,227,199,.16),0 0 18px var(--cyan);animation:pulse 2.4s ease-in-out infinite}
  @keyframes pulse{0%,100%{transform:scale(.86);opacity:.7}50%{transform:scale(1.12);opacity:1}}
  .brand h1{font-family:var(--disp);font-weight:700;font-size:19px;letter-spacing:.14em;margin:0;text-transform:uppercase}
  .brand h1 b{color:var(--cyan)}
  .brand .sub{font-family:var(--mono);font-size:11px;color:var(--dim);letter-spacing:.22em;margin-left:2px}
  .live{margin-left:auto;font-family:var(--mono);font-size:11px;color:var(--mut);
    display:flex;align-items:center;gap:8px;letter-spacing:.12em}
  .live i{width:7px;height:7px;border-radius:50%;background:var(--cyan);animation:pulse 1.6s infinite}

  /* ---------- станции (аккаунты) ---------- */
  .stations{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px;margin:22px 0}
  .station{position:relative;background:linear-gradient(180deg,var(--panel2),var(--panel));
    border:1px solid var(--line);border-radius:16px;padding:16px 16px 14px;box-shadow:var(--glow);overflow:hidden}
  .station::before{content:"";position:absolute;top:0;left:0;width:100%;height:3px;
    background:linear-gradient(90deg,var(--dim),transparent);opacity:.5}
  .station.on::before{background:linear-gradient(90deg,var(--cyan),transparent);opacity:1}
  .station.err::before{background:linear-gradient(90deg,var(--red),transparent);opacity:1}
  .st-top{display:flex;align-items:center;gap:10px}
  .st-dot{width:11px;height:11px;border-radius:50%;background:var(--dim);flex:none}
  .st-dot.on{background:var(--cyan);box-shadow:0 0 12px var(--cyan);animation:pulse 2s infinite}
  .st-dot.err{background:var(--red);box-shadow:0 0 12px var(--red)}
  .st-name{font-family:var(--disp);font-weight:600;font-size:16px;letter-spacing:.03em}
  .st-state{font-family:var(--mono);font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--mut)}
  .station.on .st-state{color:var(--cyan)} .station.err .st-state{color:var(--red)}
  .st-me{color:var(--mut);font-size:13px;margin-top:6px;min-height:18px}
  .st-err{color:var(--red);font-size:12px;font-family:var(--mono);margin-top:6px;line-height:1.35}
  .st-foot{display:flex;align-items:center;justify-content:space-between;margin-top:12px}
  .toggle{font-family:var(--disp);font-weight:600;letter-spacing:.08em;text-transform:uppercase;
    font-size:13px;border:1px solid var(--cyan-d);background:rgba(47,227,199,.12);color:var(--cyan);
    border-radius:10px;padding:8px 16px;cursor:pointer;transition:.18s}
  .toggle:hover{background:rgba(47,227,199,.2);box-shadow:0 0 16px -4px var(--cyan)}
  .toggle.stop{border-color:#3a4452;background:#1a212b;color:var(--txt)}
  .toggle.stop:hover{background:#222b37;box-shadow:none}
  .toggle:disabled{opacity:.55;cursor:default}
  .uptag{font-family:var(--mono);font-size:10px;color:var(--dim);letter-spacing:.14em}

  /* ---------- счётчики ---------- */
  .stats{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin:0 0 20px}
  .stat{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:14px 16px;position:relative;overflow:hidden}
  .stat .n{font-family:var(--mono);font-weight:700;font-size:26px;line-height:1;transition:.3s}
  .stat .l{color:var(--mut);font-size:11px;letter-spacing:.14em;text-transform:uppercase;margin-top:8px;font-family:var(--disp)}
  .stat .spark{position:absolute;right:-6px;top:-6px;width:56px;height:56px;border-radius:50%;opacity:.16;filter:blur(8px)}
  .stat.del .n{color:var(--red)} .stat.del .spark{background:var(--red)}
  .stat.edit .n{color:var(--amber)} .stat.edit .spark{background:var(--amber)}
  .stat.vo .n{color:var(--violet)} .stat.vo .spark{background:var(--violet)}
  .stat.today .n{color:var(--cyan)} .stat.today .spark{background:var(--cyan)}

  /* ---------- панель фильтров ---------- */
  .bar{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:18px}
  .seg{display:flex;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:4px;gap:2px}
  .chip{border:0;background:transparent;color:var(--mut);font-family:var(--disp);font-weight:600;
    font-size:13px;letter-spacing:.04em;padding:7px 14px;border-radius:9px;cursor:pointer;transition:.16s}
  .chip:hover{color:var(--txt)}
  .chip.act{background:var(--panel2);color:var(--txt);box-shadow:inset 0 0 0 1px var(--line2)}
  .chip[data-t=deleted].act{color:var(--red)} .chip[data-t=edited].act{color:var(--amber)}
  .chip[data-t=viewonce].act{color:var(--violet)}
  .search{flex:1;min-width:200px;position:relative}
  .search input{width:100%;background:var(--panel);border:1px solid var(--line);color:var(--txt);
    border-radius:12px;padding:10px 14px 10px 38px;font-family:var(--body);font-size:14px;outline:none;transition:.16s}
  .search input:focus{border-color:var(--cyan-d);box-shadow:0 0 0 3px rgba(47,227,199,.1)}
  .search svg{position:absolute;left:12px;top:50%;transform:translateY(-50%);opacity:.5}

  /* ---------- переключатель дней ---------- */
  .days{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:-4px 0 18px;
    overflow-x:auto;padding-bottom:2px}
  .day{flex:0 0 auto;border:1px solid var(--line);background:var(--panel);color:var(--mut);
    font-family:var(--disp);font-weight:600;font-size:12.5px;letter-spacing:.04em;
    padding:7px 13px;border-radius:11px;cursor:pointer;transition:.16s;display:flex;align-items:center;gap:8px;white-space:nowrap}
  .day:hover{color:var(--txt);border-color:var(--line2)}
  .day.act{color:#06090d;background:var(--cyan);border-color:var(--cyan)}
  .day.act .dc{background:rgba(6,9,13,.22);color:#06090d}
  .day.today:not(.act){color:var(--cyan);border-color:var(--cyan-d)}
  .day .dc{font-family:var(--mono);font-size:10.5px;background:var(--panel2);color:var(--dim);
    border-radius:6px;padding:1px 6px;letter-spacing:.02em}

  /* ---------- лента ---------- */
  .feed{display:flex;flex-direction:column;gap:12px}
  .ev{position:relative;background:linear-gradient(180deg,var(--panel2),var(--panel));
    border:1px solid var(--line);border-radius:15px;padding:14px 16px 14px 18px;box-shadow:var(--glow);overflow:hidden}
  .ev::before{content:"";position:absolute;left:0;top:0;bottom:0;width:4px;background:var(--dim)}
  .ev.deleted::before{background:linear-gradient(var(--red),transparent)}
  .ev.edited::before{background:linear-gradient(var(--amber),transparent)}
  .ev.viewonce::before{background:linear-gradient(var(--violet),transparent)}
  .ev.enter{animation:rise .5s cubic-bezier(.2,.7,.2,1) both}
  @keyframes rise{from{opacity:0;transform:translateY(10px);filter:blur(3px)}to{opacity:1;transform:none;filter:none}}
  .ev .top{display:flex;align-items:center;gap:9px;flex-wrap:wrap;font-size:13px;color:var(--mut)}
  .tag{font-family:var(--disp);font-weight:700;font-size:11px;letter-spacing:.08em;text-transform:uppercase;
    padding:3px 9px;border-radius:7px;color:#06090d}
  .tag.deleted{background:var(--red)} .tag.edited{background:var(--amber)} .tag.viewonce{background:var(--violet)}
  .who{color:var(--txt);font-weight:700;font-family:var(--disp);letter-spacing:.02em}
  .pf{font-family:var(--mono);font-size:11px;color:var(--mut);border:1px solid var(--line2);
    border-radius:6px;padding:1px 7px;letter-spacing:.02em}
  .pf.acct{color:var(--cyan);border-color:var(--cyan-d)}
  .ts{margin-left:auto;font-family:var(--mono);font-size:11px;color:var(--dim);letter-spacing:.06em;white-space:nowrap}
  .body{margin-top:9px;white-space:pre-wrap;word-break:break-word;font-size:14.5px}
  .old{color:var(--mut);text-decoration:line-through;opacity:.75}
  .arrow{color:var(--amber);font-family:var(--mono);margin:3px 0;opacity:.8}
  img.m,video.m{max-width:340px;max-height:340px;border-radius:12px;margin-top:10px;display:block;
    border:1px solid var(--line2);box-shadow:0 14px 30px -18px #000}
  .file{margin-top:10px;display:inline-flex;gap:8px;align-items:center;color:var(--cyan);
    text-decoration:none;font-family:var(--mono);font-size:13px;border:1px solid var(--cyan-d);
    border-radius:9px;padding:7px 12px;background:rgba(47,227,199,.08)}
  .empty{text-align:center;color:var(--mut);padding:70px 20px}
  .empty .big{font-family:var(--disp);font-size:18px;letter-spacing:.1em;color:var(--txt);text-transform:uppercase}
  .empty .sm{font-family:var(--mono);font-size:12px;color:var(--dim);margin-top:8px;letter-spacing:.06em}

  /* ---------- тосты ---------- */
  #toasts{position:fixed;right:20px;bottom:20px;z-index:50;display:flex;flex-direction:column;gap:10px}
  .toast{font-family:var(--mono);font-size:13px;padding:12px 16px;border-radius:12px;
    background:var(--panel2);border:1px solid var(--line2);box-shadow:0 18px 40px -20px #000;
    max-width:340px;animation:rise .35s both}
  .toast.err{border-color:var(--red);color:#ffd2d6} .toast.ok{border-color:var(--cyan-d);color:#bff6ee}
  #login{display:none;position:fixed;inset:0;z-index:60;align-items:center;justify-content:center;
    background:rgba(5,8,12,.86);backdrop-filter:blur(6px)}
  .loginbox{background:var(--panel2);border:1px solid var(--line2);border-radius:16px;padding:26px;
    width:300px;box-shadow:var(--glow)}
  .lh{font-family:var(--disp);font-weight:700;letter-spacing:.1em;text-transform:uppercase;margin-bottom:14px}
  .loginbox input{width:100%;background:var(--bg);border:1px solid var(--line);color:var(--txt);
    border-radius:10px;padding:11px 13px;font-family:var(--mono);outline:none}
  .loginbox button{width:100%;margin-top:12px;border:0;border-radius:10px;padding:11px;font-family:var(--disp);
    font-weight:700;letter-spacing:.08em;text-transform:uppercase;background:var(--cyan);color:#06090d;cursor:pointer}
  .lhint{color:var(--red);font-family:var(--mono);font-size:12px;margin-top:10px;min-height:14px}
  @media(max-width:680px){.stats{grid-template-columns:repeat(2,1fr)}}
</style></head>
<body>
<div class="grain"></div>
<div class="wrap">
  <header>
    <div class="brand">
      <span class="sig"></span>
      <h1>DIA<b>SLOG</b> Intercept</h1>
      <span class="sub">// LOCAL</span>
      <span class="live"><i></i><span id="clock">CONNECTING…</span></span>
    </div>
  </header>

  <div class="stations" id="stations"></div>
  <div class="stats" id="stats"></div>

  <div class="bar">
    <div class="seg">
      <button class="chip act" data-t="all">Все</button>
      <button class="chip" data-t="deleted">🗑 Удалённые</button>
      <button class="chip" data-t="edited">✏ Изменённые</button>
      <button class="chip" data-t="viewonce">👁 Одноразовые</button>
    </div>
    <div class="search">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg>
      <input id="q" placeholder="Поиск: текст · имя · чат">
    </div>
  </div>

  <div class="days" id="days"></div>

  <div class="feed" id="feed"><div class="empty"><div class="big">Загрузка…</div></div></div>
</div>
<div id="login"><div class="loginbox">
  <div class="lh">DIASLOG · доступ</div>
  <input id="pw" type="password" placeholder="Пароль">
  <button id="pwbtn">Войти</button>
  <div class="lhint" id="lhint"></div>
</div></div>
<div id="toasts"></div>

<script>
const TG=(window.Telegram&&window.Telegram.WebApp)?window.Telegram.WebApp:null;
const INIT=TG?TG.initData:"";
const MINI=!!INIT;
if(TG){try{TG.ready();TG.expand();}catch(e){}}
let ADMINPW=sessionStorage.getItem("pw")||"";
let TYPE="all", Q="", SIG="", SEEN=new Set();
const TZ=new Date().getTimezoneOffset()*60;   // смещение пояса браузера, сек (для границ суток)
let DAY="", DAYSIG="";                          // выбранный день (YYYY-MM-DD), "" = ещё не задан
function authHeaders(){const h={};if(INIT)h["X-Telegram-Init-Data"]=INIT;else if(ADMINPW)h["X-Admin-Password"]=ADMINPW;return h;}
function authQS(){return INIT?("i="+encodeURIComponent(INIT)):(ADMINPW?("pw="+encodeURIComponent(ADMINPW)):"");}
function showLogin(){const l=document.getElementById("login");if(l)l.style.display="flex";}
async function jget(path){const r=await fetch(path,{headers:authHeaders()});
  if(r.status===401){ if(MINI){toast("Нет доступа для этого аккаунта","err");} else {showLogin();} throw new Error("auth"); }
  return r;}
const esc=s=>(s==null?"":String(s)).replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
const ago=t=>{const d=Date.now()/1000-t;
  if(d<60)return"только что"; if(d<3600)return Math.floor(d/60)+" мин";
  if(d<86400)return Math.floor(d/3600)+" ч"; return new Date(t*1000).toLocaleDateString("ru")};
const pad2=n=>String(n).padStart(2,"0");
const dayStr=d=>d.getFullYear()+"-"+pad2(d.getMonth()+1)+"-"+pad2(d.getDate());
function dayLabel(s){
  const today=dayStr(new Date());
  const y=new Date(); y.setDate(y.getDate()-1);
  if(s===today)return"Сегодня"; if(s===dayStr(y))return"Вчера";
  const[Y,M,D]=s.split("-"); return D+"."+M;
}
function toast(msg,kind){const c=document.getElementById("toasts");
  const el=document.createElement("div");el.className="toast "+(kind||"");el.textContent=msg;
  c.appendChild(el);setTimeout(()=>{el.style.opacity=0;el.style.transition=".4s";setTimeout(()=>el.remove(),400)},4200);}

async function loadStatus(){
  let d; try{ d=await (await jget("/api/status")).json(); }catch(e){ return; }
  document.getElementById("clock").textContent=(MINI?"TG · ":"LIVE · ")+new Date().toLocaleTimeString("ru");
  // станции
  const wrap=document.getElementById("stations");
  wrap.innerHTML=d.profiles.map(p=>{
    const cls=p.running?"on":(p.last_error?"err":"");
    const state=p.running?"Активен":(p.last_error?"Ошибка":"Остановлен");
    const err=(!p.running&&p.last_error)?`<div class="st-err">${esc(p.last_error)}</div>`:"";
    const me=p.me_name?esc(p.me_name):(p.session?"сессия готова":"нет сессии — войди через терминал");
    return `<div class="station ${cls}">
      <div class="st-top"><span class="st-dot ${cls}"></span>
        <span class="st-name">${esc(p.label)}</span>
        <span class="st-state" style="margin-left:auto">${state}</span></div>
      <div class="st-me">${me}</div>${err}
      <div class="st-foot">
        <span class="uptag">${p.configured?"CFG OK":"НЕ НАСТРОЕН"}</span>
        ${MINI?"":`<button class="toggle ${p.running?'stop':''}" data-n="${p.name}" data-r="${p.running}" ${p.configured?"":"disabled"}>${p.running?'Стоп':'Старт'}</button>`}
      </div></div>`;
  }).join("");
  wrap.querySelectorAll(".toggle").forEach(b=>b.onclick=async()=>{
    const run=b.dataset.r==="true";
    b.disabled=true;b.textContent=run?"Останавливаю…":"Запускаю…";
    try{
      const res=await (await fetch("/api/"+(run?"stop":"start")+"?profile="+encodeURIComponent(b.dataset.n),{method:"POST",headers:authHeaders()})).json();
      if(res.ok){ toast((run?"Остановлен: ":"Запущен: ")+b.dataset.n,"ok"); }
      else{ toast("Ошибка ["+b.dataset.n+"]: "+(res.error||"неизвестно"),"err"); }
    }catch(e){ toast("Сбой запроса: "+e,"err"); }
    await loadStatus(); loadFeed(true);
  });
  // счётчики
  const s={total:0,deleted:0,edited:0,viewonce:0,today:0};
  d.profiles.forEach(p=>{for(const k in s)s[k]+=(p.stats[k]||0)});
  document.getElementById("stats").innerHTML=
    [["total","Всего","tot"],["deleted","Удалённые","del"],["edited","Изменённые","edit"],
     ["viewonce","Одноразовые","vo"],["today","За сутки","today"]]
    .map(([k,l,c])=>`<div class="stat ${c}"><div class="spark"></div>
       <div class="n">${s[k]}</div><div class="l">${l}</div></div>`).join("");
}

async function loadDays(){
  let d; try{ d=await (await jget(`/api/days?profile=all&tz=${TZ}`)).json(); }catch(e){ return; }
  const today=dayStr(new Date());
  const map={}; (d.days||[]).forEach(x=>map[x.day]=x.count);
  if(!(today in map)) map[today]=0;                 // сегодня всегда доступно, даже пустое
  const days=Object.keys(map).sort().reverse();
  if(!DAY || !days.includes(DAY)) DAY=days[0];       // по умолчанию — самый новый (сегодня)
  const sig=DAY+"|"+days.map(s=>s+":"+map[s]).join(",");
  if(sig===DAYSIG) return; DAYSIG=sig;
  const box=document.getElementById("days");
  box.innerHTML=days.map(s=>{
    const cls=(s===DAY?"act ":"")+(s===today?"today":"");
    const cnt=map[s]?`<span class="dc">${map[s]}</span>`:"";
    return `<button class="day ${cls}" data-d="${s}">${dayLabel(s)}${cnt}</button>`;
  }).join("");
  box.querySelectorAll(".day").forEach(b=>b.onclick=()=>{
    if(DAY===b.dataset.d)return;
    DAY=b.dataset.d; DAYSIG=""; loadDays(); loadFeed(true);
  });
}

async function loadFeed(force){
  let d; try{ d=await (await jget(`/api/feed?profile=all&type=${TYPE}&q=${encodeURIComponent(Q)}&day=${encodeURIComponent(DAY)}&tz=${TZ}`)).json(); }catch(e){ return; }
  const top=d.events[0]?d.events[0].id+"@"+d.events[0].profile:"";
  const sig=TYPE+"|"+Q+"|"+DAY+"|"+top+"|"+d.events.length;
  if(!force && sig===SIG) return; SIG=sig;
  const f=document.getElementById("feed");
  if(!d.events.length){
    const sm=DAY===dayStr(new Date())
      ?"за сегодня пока пусто — жду удалений, правок и одноразовых"
      :"за этот день перехватов нет";
    f.innerHTML='<div class="empty"><div class="big">Эфир чист</div><div class="sm">'+sm+'</div></div>';return}
  const tags={deleted:"Удалено",edited:"Изменено",viewonce:"Одноразовое"};
  f.innerHTML=d.events.map((e,i)=>{
    const key=e.profile+":"+e.id; const fresh=!SEEN.has(key); SEEN.add(key);
    let media="";
    if(e.media_file){const _q=authQS();const u=`/media/${encodeURIComponent(e.profile)}/${encodeURIComponent(e.media_file)}`+(_q?("?"+_q):"");
      if(e.media_type==="photo")media=`<img class="m" loading="lazy" src="${u}">`;
      else if(e.media_type==="video"||e.media_type==="video_note")media=`<video class="m" src="${u}" controls></video>`;
      else media=`<a class="file" href="${u}" target="_blank">📎 ${esc(e.media_type||'файл')}</a>`;}
    let body="";
    if(e.type==="edited")body=`<div class="old">${esc(e.old_text)||'(пусто)'}</div><div class="arrow">→</div><div>${esc(e.text)||'(пусто)'}</div>`;
    else if(e.text)body=esc(e.text);
    const where=e.chat_title?`<span class="pf">${esc(e.chat_title)}</span>`:"";
    const dly=fresh?`style="animation-delay:${Math.min(i,8)*45}ms"`:"";
    return `<div class="ev ${e.type} ${fresh?'enter':''}" ${dly}><div class="top">
      <span class="tag ${e.type}">${tags[e.type]||e.type}</span>
      <span class="who">${esc(e.sender_name)}</span>${where}
      <span class="pf acct">${esc(e.profile_label)}</span>
      <span class="ts">${ago(e.created_at)}</span></div>
      <div class="body">${body}</div>${media}</div>`;
  }).join("");
}

document.querySelectorAll(".chip").forEach(c=>c.onclick=()=>{
  document.querySelectorAll(".chip").forEach(x=>x.classList.remove("act"));
  c.classList.add("act");TYPE=c.dataset.t;loadFeed(true);});
document.getElementById("q").oninput=e=>{Q=e.target.value;loadFeed(true);};

const pwbtn=document.getElementById("pwbtn");
if(pwbtn)pwbtn.onclick=()=>{
  ADMINPW=document.getElementById("pw").value;
  sessionStorage.setItem("pw",ADMINPW);
  document.getElementById("login").style.display="none";
  loadDays().then(()=>{loadStatus();loadFeed(true);});
};
const pwin=document.getElementById("pw");
if(pwin)pwin.onkeydown=e=>{ if(e.key==="Enter")pwbtn.click(); };

loadDays().then(()=>{loadStatus();loadFeed(true);});
setInterval(loadFeed,4000);
setInterval(loadStatus,7000);
setInterval(loadDays,7000);
</script>
</body></html>"""


# ---------------- HTTP-обработчик ----------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _scope(self, qs=None):
        """Кто запрашивает и что ему показывать.
        ("user", profile_name) — пользователь Telegram Mini App (только свой профиль);
        ("admin", None)        — вход по паролю в браузере (все профили);
        None                   — доступа нет (401)."""
        qs = qs or {}
        init = self.headers.get("X-Telegram-Init-Data") or qs.get("i", [None])[0]
        if init:
            name = validate_init_data(init)
            return ("user", name) if name else None
        pw = self.headers.get("X-Admin-Password") or qs.get("pw", [None])[0]
        if PASSWORD:
            return ("admin", None) if pw == PASSWORD else None
        return ("admin", None)  # пароль не задан (локальная отладка)

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        if u.path == "/":  # сама страница без секретов — отдаём всегда (нужно для Mini App)
            return self._send(200, PAGE, "text/html; charset=utf-8")
        scope = self._scope(qs)
        if scope is None:
            return self._send(401, {"error": "auth"})
        _, only = scope
        if u.path == "/api/status":
            return self._send(200, {"profiles": status_list(only)})
        if u.path == "/api/feed":
            sel = only or qs.get("profile", ["all"])[0]
            return self._send(200, {"events": feed_list(
                sel, qs.get("type", ["all"])[0], qs.get("q", [""])[0],
                qs.get("day", [""])[0], _int_qs(qs, "tz"))})
        if u.path == "/api/days":
            sel = only or qs.get("profile", ["all"])[0]
            return self._send(200, {"days": days_list(sel, _int_qs(qs, "tz"))})
        if u.path.startswith("/media/"):
            return self._serve_media(u.path, only)
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        scope = self._scope(qs)
        if scope is None:
            return self._send(401, {"error": "auth"})
        _, only = scope
        name = qs.get("profile", [""])[0]
        if only and name != only:  # пользователь управляет только своим профилем
            return self._send(403, {"error": "нет доступа"})
        if u.path == "/api/start":
            return self._send(200, control(name, start=True))
        if u.path == "/api/stop":
            return self._send(200, control(name, start=False))
        return self._send(404, {"error": "not found"})

    def _serve_media(self, path, only=None):
        parts = path.split("/", 3)  # ['', 'media', profile, basename]
        if len(parts) < 4:
            return self._send(404, {"error": "bad path"})
        pname, fname = unquote(parts[2]), unquote(parts[3])
        if only and pname != only:
            return self._send(403, {"error": "нет доступа"})
        prof = PROFILES.get(pname)
        if not prof or "/" in fname or "\\" in fname or ".." in fname:
            return self._send(404, {"error": "bad file"})
        fpath = os.path.join(prof.media_dir, fname)
        if not os.path.exists(fpath):
            return self._send(404, {"error": "no file"})
        ctype = mimetypes.guess_type(fpath)[0] or "application/octet-stream"
        with open(fpath, "rb") as fh:
            data = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


# ---------------- логика ----------------
def validate_init_data(raw):
    """Проверяет подпись Telegram WebApp initData токенами ботов.
    Возвращает имя профиля (по owner_id проверенного пользователя) или None."""
    if not raw:
        return None
    try:
        pairs = dict(parse_qsl(raw, keep_blank_values=True))
        recv = pairs.pop("hash", None)
        if not recv:
            return None
        check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
        uid = json.loads(pairs["user"]).get("id") if pairs.get("user") else None
        for prof in PROFILES.values():
            if not prof.bot_token:
                continue
            secret = hmac.new(b"WebAppData", prof.bot_token.encode(), hashlib.sha256).digest()
            calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
            if hmac.compare_digest(calc, recv):
                if uid is None:
                    return None
                for n2, p2 in PROFILES.items():
                    if p2.owner_id and int(p2.owner_id) == int(uid):
                        return n2
                return None
        return None
    except Exception:
        return None


def status_list(only=None):
    out = []
    for name, prof in PROFILES.items():
        if only and name != only:
            continue
        cap = CAPTURERS.get(name)
        out.append({
            "name": name, "label": prof.label,
            "configured": prof.configured, "session": prof.session_exists,
            "running": bool(cap and cap.running),
            "me_name": cap.me_name if cap else None,
            "last_error": cap.last_error if cap else None,
            "stats": store.stats(prof.db_path),
        })
    return out


def _int_qs(qs, key, default=0):
    try:
        return int(qs.get(key, [default])[0])
    except (TypeError, ValueError):
        return default


def feed_list(profile_sel, type_, q, day="", tz=0):
    names = list(PROFILES) if profile_sel in ("all", "") else [profile_sel]
    events = []
    for name in names:
        prof = PROFILES.get(name)
        if not prof:
            continue
        for e in store.query_events(prof.db_path, type_, q, day=day or None, tz=tz):
            e["profile"] = name
            e["profile_label"] = prof.label
            events.append(e)
    events.sort(key=lambda e: e["created_at"], reverse=True)
    return events[:200]


def days_list(profile_sel, tz=0):
    """Список дней (по всем выбранным профилям) с количеством событий, новые сверху."""
    names = list(PROFILES) if profile_sel in ("all", "") else [profile_sel]
    merged = {}
    for name in names:
        prof = PROFILES.get(name)
        if not prof:
            continue
        for day, n in store.query_days(prof.db_path, tz).items():
            merged[day] = merged.get(day, 0) + n
    return [{"day": d, "count": merged[d]} for d in sorted(merged, reverse=True)]


def control(name, start):
    cap = CAPTURERS.get(name)
    if not cap:
        return {"ok": False, "error": "нет такого профиля"}
    try:
        coro = cap.start() if start else cap.stop()
        fut = asyncio.run_coroutine_threadsafe(coro, LOOP)
        fut.result(timeout=90)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def serve_http():
    try:
        ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
    except OSError as e:
        log.error("!!! Порт %d занят (%s). Уже запущен другой app.py — закрой его. Выходим.",
                  PORT, e)
        os._exit(1)


async def amain():
    global LOOP, PROFILES, CAPTURERS
    LOOP = asyncio.get_event_loop()
    PROFILES = profiles.discover()
    if not PROFILES:
        log.warning("Профилей нет (нет .env в %s). Дашборд поднят, но аккаунтов нет. "
                    "Через консоль создай .env / .env.friend в этой папке, войди "
                    "(python main.py [friend]) и перезапусти контейнер.", profiles.CONFIG_DIR)
    for name, prof in PROFILES.items():
        CAPTURERS[name] = Capturer(prof, Store)

    if os.getenv("DIASLOG_NO_AUTOSTART") != "1":
        for name, cap in CAPTURERS.items():
            prof = cap.profile
            if prof.configured and prof.session_exists:
                try:
                    await cap.start()
                except Exception as e:
                    log.warning("[%s] не удалось запустить: %s", name, e)
            elif prof.configured:
                cap.last_error = "Нет сессии. Войди: python main.py " + (
                    "" if name == "default" else name)

    if HOST != "127.0.0.1" and not PASSWORD:
        log.warning("!!! ВНИМАНИЕ: дашборд слушает %s БЕЗ пароля. "
                    "Задай переменную DIASLOG_PASSWORD, иначе ленту увидит кто угодно!", HOST)

    threading.Thread(target=serve_http, daemon=True).start()
    log.info("Дашборд открыт:  http://localhost:%d  (host=%s)", PORT, HOST)
    log.info("Останов — Ctrl+C.")
    await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        log.info("Остановлено пользователем.")
