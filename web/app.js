"use strict";

const view = document.getElementById("view");
const state = { cfg: null, health: null, liveTimers: [] };

// ---- api helpers -------------------------------------------------------
// Auth is a session cookie (set by /api/login), sent automatically on every
// request incl. <img> streams — so no token juggling in the URLs.
async function api(path, opts = {}) {
  const headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
  const res = await fetch(path, Object.assign({ credentials: "same-origin" }, opts, { headers }));
  if (res.status === 401) { document.body.classList.add("auth-locked"); renderLogin(); throw new Error("unauthorized"); }
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
  return res.status === 204 ? null : res.json();
}
function streamUrl(cam) { return `/api/cameras/${encodeURIComponent(cam)}/stream.mjpg`; }
function snapUrl(file) { return `/api/snapshots/${encodeURIComponent(file)}`; }

// ---- ui helpers --------------------------------------------------------
function el(html) { const d = document.createElement("div"); d.innerHTML = html.trim(); return d.firstChild; }
function toast(msg, kind = "") {
  const t = el(`<div class="toast ${kind}">${msg}</div>`);
  document.body.appendChild(t);
  requestAnimationFrame(() => t.classList.add("show"));
  setTimeout(() => { t.classList.remove("show"); setTimeout(() => t.remove(), 250); }, 2600);
}
function fmtTime(iso) {
  try { return new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }); }
  catch { return iso; }
}
function clearLive() {
  state.liveTimers.forEach(clearInterval); state.liveTimers = [];
  // blank any live <img> so the browser tears down the MJPEG connection
  document.querySelectorAll(".cam-card img").forEach(i => { i.src = ""; });
}

// ---- status poll -------------------------------------------------------
async function pollHealth() {
  const dot = document.getElementById("statusDot"), txt = document.getElementById("statusText");
  try {
    state.health = await api("/api/health");
    const s = state.health.status;
    dot.className = "dot " + (s.error ? "err" : s.running ? "ok" : "");
    txt.textContent = s.error ? "error" : s.running ? `${s.cameras.length} cam · ${s.backend}${s.claude ? " · claude" : ""}` : "idle";
  } catch (e) {
    dot.className = "dot err"; txt.textContent = "offline";
  }
}

// ---- LIVE --------------------------------------------------------------
async function renderLive() {
  clearLive();
  await pollHealth();
  const cams = (state.health && state.health.status.cameras) || [];
  if (!cams.length) {
    view.innerHTML = `<div class="empty"><div class="big">▦</div><p>No cameras reporting yet.<br>Check the Settings tab and the service logs.</p></div>`;
    return;
  }
  view.innerHTML = `<div class="page-title">Live</div><div class="grid" id="camGrid"></div>`;
  const grid = document.getElementById("camGrid");
  cams.forEach(cam => {
    const card = el(`<div class="card cam-card">
      <img alt="${cam}" src="${streamUrl(cam)}" onerror="this.style.opacity=.25" />
      <div class="meta"><span class="name">${cam}</span> <span class="badge live">● LIVE</span></div>
    </div>`);
    grid.appendChild(card);
  });
}

// ---- EVENTS ------------------------------------------------------------
async function renderEvents() {
  clearLive();
  view.innerHTML = `<div class="page-title">Events</div>
    <div class="stats" id="stats"></div>
    <div class="filters">
      <select id="fCam"><option value="">All cameras</option></select>
      <input id="fLabel" placeholder="Filter label e.g. person" />
    </div>
    <div class="grid" id="evGrid"></div>`;
  document.getElementById("fCam").onchange = loadEvents;
  document.getElementById("fLabel").oninput = debounce(loadEvents, 350);
  await loadEvents();
}
async function loadEvents() {
  const cam = document.getElementById("fCam").value;
  const label = document.getElementById("fLabel").value.trim();
  const q = new URLSearchParams({ limit: 100 });
  if (cam) q.set("camera", cam);
  if (label) q.set("label", label);
  let data;
  try { data = await api("/api/events?" + q); } catch (e) { toast(e.message, "err"); return; }

  const st = data.stats;
  document.getElementById("stats").innerHTML =
    `<div class="stat"><div class="n">${st.total}</div><div class="l">Total events</div></div>
     <div class="stat"><div class="n">${st.last_24h}</div><div class="l">Last 24h</div></div>
     <div class="stat"><div class="n">${Object.keys(st.by_camera).length}</div><div class="l">Cameras</div></div>`;

  const sel = document.getElementById("fCam");
  const cur = sel.value;
  sel.innerHTML = `<option value="">All cameras</option>` +
    Object.keys(st.by_camera).map(c => `<option value="${c}">${c}</option>`).join("");
  sel.value = cur;

  const grid = document.getElementById("evGrid");
  if (!data.events.length) {
    grid.innerHTML = `<div class="empty" style="grid-column:1/-1"><div class="big">◷</div><p>No events yet. Walk in front of a camera.</p></div>`;
    return;
  }
  grid.innerHTML = "";
  data.events.forEach(ev => {
    const card = el(`<div class="card event-card">
      ${ev.snapshot ? `<img loading="lazy" src="${snapUrl(ev.snapshot)}" alt="">` : ""}
      <div class="meta">
        <span class="badge">${ev.camera}</span>
        <div class="msg">${escapeHtml(ev.message)}</div>
        <div class="time">${escapeHtml(ev.labels)} · ${fmtTime(ev.iso)}</div>
      </div></div>`);
    card.onclick = () => openEvent(ev);
    grid.appendChild(card);
  });
}
function openEvent(ev) {
  const c = document.getElementById("modalContent");
  c.innerHTML = `${ev.snapshot ? `<img src="${snapUrl(ev.snapshot)}">` : ""}
    <div class="detail">
      <h3>${escapeHtml(ev.labels)}</h3>
      <div class="kv">${escapeHtml(ev.message)}</div>
      <div class="kv">Camera: ${ev.camera}</div>
      <div class="kv">${fmtTime(ev.iso)}${ev.max_conf ? " · conf " + Math.round(ev.max_conf * 100) + "%" : ""}</div>
      <div class="actions" style="position:static">
        <button class="btn-danger" id="delEv">Delete event</button>
      </div>
    </div>`;
  document.getElementById("delEv").onclick = async () => {
    try { await api("/api/events/" + ev.id, { method: "DELETE" }); closeModal(); loadEvents(); toast("Deleted", "ok"); }
    catch (e) { toast(e.message, "err"); }
  };
  showModal();
}

// ---- SETTINGS ----------------------------------------------------------
async function renderSettings() {
  clearLive();
  try { state.cfg = await api("/api/config"); } catch (e) { view.innerHTML = `<div class="empty">${e.message}</div>`; return; }
  const cfg = state.cfg;
  const classes = (cfg.detection.classes_of_interest || []).join(", ");
  view.innerHTML = `
    <div class="page-title">Settings</div>

    <div class="section-title">Cameras</div>
    <div class="card" style="padding:14px"><div id="camList"></div>
      <button class="btn-ghost" id="addCam">+ Add camera</button></div>

    <div class="section-title">Motion sensitivity</div>
    <div class="card" style="padding:14px">
      <div class="row">
        <div class="field"><label>Min change area (px)</label><input type="number" id="m_area" value="${cfg.motion.min_area}"></div>
        <div class="field"><label>Pixel threshold</label><input type="number" id="m_thr" value="${cfg.motion.threshold}"></div>
        <div class="field"><label>Warmup frames</label><input type="number" id="m_warm" value="${cfg.motion.warmup_frames}"></div>
      </div><div class="hint">Higher area / threshold = fewer, stronger triggers.</div>
    </div>

    <div class="section-title">Detection</div>
    <div class="card" style="padding:14px">
      <div class="row">
        <div class="field"><label>Backend</label><select id="d_back">
          <option value="yolo">yolo (offline object detection)</option>
          <option value="none">none (motion only)</option></select></div>
        <div class="field"><label>Confidence</label><input type="number" step="0.05" min="0" max="1" id="d_conf" value="${cfg.detection.confidence}"></div>
        <div class="field"><label>Cooldown (s)</label><input type="number" id="d_cool" value="${cfg.detection.cooldown_seconds}"></div>
      </div>
      <div class="field"><label>Classes of interest (comma separated)</label><input id="d_classes" value="${classes}"></div>
    </div>

    <div class="section-title">Push notifications (this device)</div>
    <div class="card" style="padding:14px">
      <div id="pushStatus" class="hint">Checking…</div>
      <div class="row" style="margin-top:10px">
        <button class="btn-primary" id="pushEnable" hidden>Enable on this device</button>
        <button class="btn-ghost" id="pushDisable" hidden>Disable</button>
        <button class="btn-ghost" id="pushTest" hidden>Send test</button>
      </div>
      <div class="hint" style="margin-top:8px">Native browser notifications — no third-party app.
        Requires HTTPS; on iPhone, add PiEYE to your Home Screen first.</div>
    </div>

    <div class="section-title">Notifications (ntfy)</div>
    <div class="card" style="padding:14px">
      <div class="field inline"><input type="checkbox" id="n_en" ${cfg.notify.ntfy_enabled !== false ? "checked" : ""}><label style="margin:0">Also send via ntfy</label></div>
      <div class="row">
        <div class="field"><label>ntfy server</label><input id="n_srv" value="${cfg.notify.ntfy_server}"></div>
        <div class="field"><label>Topic</label><input id="n_topic" value="${cfg.notify.ntfy_topic}"></div>
      </div>
      <div class="row">
        <div class="field"><label>Priority</label><select id="n_pri">
          ${["min","low","default","high","max"].map(p => `<option ${cfg.notify.priority == p ? "selected" : ""}>${p}</option>`).join("")}
        </select></div>
        <div class="field"><label>Min confidence to alert</label><input type="number" step="0.05" min="0" max="1" id="n_min" value="${cfg.notify.min_confidence_to_alert}"></div>
      </div>
    </div>

    <div class="section-title">Claude description tier</div>
    <div class="card" style="padding:14px">
      <div class="field inline"><input type="checkbox" id="c_en" ${cfg.claude.enabled ? "checked" : ""}><label style="margin:0">Enable natural-language descriptions (needs ANTHROPIC_API_KEY on the Pi)</label></div>
      <div class="field"><label>Model</label><input id="c_model" value="${cfg.claude.model || ""}"></div>
    </div>

    <div class="section-title">Server &amp; storage</div>
    <div class="card" style="padding:14px">
      <div class="row">
        <div class="field"><label>Retention (days)</label><input type="number" id="s_ret" value="${cfg.storage.retention_days}"></div>
        <div class="field"><label>Max stored events</label><input type="number" id="s_max" value="${cfg.storage.max_events}"></div>
      </div>
      <div class="hint">Bind host/port changes (${cfg.server.host}:${cfg.server.port}) require a service restart.</div>
    </div>

    <div class="section-title">Account</div>
    <div class="card" style="padding:14px">
      <div id="acct" class="hint">Checking login status…</div>
      <div style="margin-top:10px"><button class="btn-ghost" id="signout" hidden>Sign out</button></div>
    </div>

    <div class="actions savebar">
      <button class="btn-ghost" id="reload">Revert</button>
      <button class="btn-primary" id="save">Save &amp; apply</button>
    </div>`;

  renderAccount();
  renderPush();
  document.getElementById("d_back").value = cfg.detection.backend || "yolo";
  renderCamRows();
  document.getElementById("addCam").onclick = () => {
    state.cfg.cameras.push({ id: "cam" + state.cfg.cameras.length, source: state.cfg.cameras.length, rotate: 0 });
    renderCamRows();
  };
  document.getElementById("reload").onclick = renderSettings;
  document.getElementById("save").onclick = saveSettings;
}
function renderCamRows() {
  const wrap = document.getElementById("camList");
  wrap.innerHTML = "";
  state.cfg.cameras.forEach((c, i) => {
    const row = el(`<div class="cam-row">
      <input value="${c.id}" placeholder="name" data-i="${i}" data-k="id">
      <input value="${c.source}" placeholder="index or rtsp url" data-i="${i}" data-k="source">
      <select data-i="${i}" data-k="rotate">${[0,90,180,270].map(r => `<option ${c.rotate==r?"selected":""}>${r}</option>`).join("")}</select>
      <button class="btn-danger del" data-i="${i}">✕</button></div>`);
    wrap.appendChild(row);
  });
  wrap.querySelectorAll("input,select").forEach(inp => inp.onchange = e => {
    const { i, k } = e.target.dataset; let v = e.target.value;
    if (k === "rotate") v = parseInt(v);
    if (k === "source" && /^\d+$/.test(v)) v = parseInt(v);
    state.cfg.cameras[i][k] = v;
  });
  wrap.querySelectorAll(".del").forEach(b => b.onclick = e => {
    state.cfg.cameras.splice(parseInt(e.target.dataset.i), 1); renderCamRows();
  });
}
async function saveSettings() {
  const cfg = state.cfg;
  const num = id => parseFloat(document.getElementById(id).value);
  cfg.motion.min_area = num("m_area"); cfg.motion.threshold = num("m_thr"); cfg.motion.warmup_frames = num("m_warm");
  cfg.detection.backend = document.getElementById("d_back").value;
  cfg.detection.confidence = num("d_conf"); cfg.detection.cooldown_seconds = num("d_cool");
  cfg.detection.classes_of_interest = document.getElementById("d_classes").value.split(",").map(s => s.trim()).filter(Boolean);
  cfg.notify.ntfy_enabled = document.getElementById("n_en").checked;
  cfg.notify.ntfy_server = document.getElementById("n_srv").value.trim();
  cfg.notify.ntfy_topic = document.getElementById("n_topic").value.trim();
  cfg.notify.priority = document.getElementById("n_pri").value;
  cfg.notify.min_confidence_to_alert = num("n_min");
  cfg.claude.enabled = document.getElementById("c_en").checked;
  cfg.claude.model = document.getElementById("c_model").value.trim();
  cfg.storage.retention_days = num("s_ret"); cfg.storage.max_events = num("s_max");
  try {
    await api("/api/config", { method: "PUT", body: JSON.stringify(cfg) });
    toast("Saved — monitor reloaded", "ok"); pollHealth();
  } catch (e) { toast(e.message, "err"); }
}

async function renderAccount() {
  const box = document.getElementById("acct"), btn = document.getElementById("signout");
  let me; try { me = await api("/api/me"); } catch { return; }
  if (me.auth_required) {
    box.innerHTML = `Signed in as <strong>${escapeHtml(me.username || "user")}</strong>. ` +
      `Change the password on the Pi with <code>python -m vision.set_password</code>.`;
    btn.hidden = false;
    btn.onclick = async () => { await fetch("/api/logout", { method: "POST", credentials: "same-origin" }); location.reload(); };
  } else {
    box.innerHTML = `Login is <strong>disabled</strong> — the UI is open to anyone on the network. ` +
      `Enable it on the Pi with <code>python -m vision.set_password</code>.`;
  }
}

// ---- web push ----------------------------------------------------------
function b64ToUint8(b64) {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from([...raw].map(c => c.charCodeAt(0)));
}

async function renderPush() {
  const box = document.getElementById("pushStatus");
  const bEn = document.getElementById("pushEnable");
  const bOff = document.getElementById("pushDisable");
  const bTest = document.getElementById("pushTest");
  if (!box) return;
  const show = (en, off, test) => { bEn.hidden = !en; bOff.hidden = !off; bTest.hidden = !test; };

  // Hard requirements first — be explicit instead of failing silently.
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    box.innerHTML = "⚠️ This browser doesn't support web push."; show(0, 0, 0); return;
  }
  if (!window.isSecureContext) {
    box.innerHTML = "🔒 Push needs <strong>HTTPS</strong>. You're on plain http, so the browser " +
      "blocks it. See <code>docs/tls.md</code> for free options (Tailscale, DuckDNS).";
    show(0, 0, 0); return;
  }
  if (Notification.permission === "denied") {
    box.innerHTML = "🚫 Notifications are blocked for this site — re-allow them in your browser settings.";
    show(0, 0, 0); return;
  }

  let st; try { st = await api("/api/push/status"); } catch { return; }
  const reg = await navigator.serviceWorker.getRegistration();
  const sub = reg ? await reg.pushManager.getSubscription() : null;

  if (sub) {
    box.innerHTML = `✅ Push is <strong>on</strong> for this device (${st.subscriptions} device(s) subscribed).`;
    show(0, 1, 1);
  } else {
    box.innerHTML = st.enabled
      ? `Push is available (${st.subscriptions} other device(s) subscribed). Enable it here.`
      : "Push is off. Enabling generates a key on the Pi and subscribes this device.";
    show(1, 0, 0);
  }

  bEn.onclick = async () => {
    try {
      if (await Notification.requestPermission() !== "granted") { toast("Permission denied", "err"); return; }
      const { public_key } = await api("/api/push/enable", { method: "POST" });
      const r = await navigator.serviceWorker.register("/sw.js");
      await navigator.serviceWorker.ready;
      const s = await r.pushManager.subscribe({
        userVisibleOnly: true, applicationServerKey: b64ToUint8(public_key) });
      await api("/api/push/subscribe", { method: "POST", body: JSON.stringify(s.toJSON()) });
      toast("Push enabled", "ok"); renderPush();
    } catch (e) { toast(e.message || "Could not enable push", "err"); }
  };
  bOff.onclick = async () => {
    try {
      const r = await navigator.serviceWorker.getRegistration();
      const s = r && await r.pushManager.getSubscription();
      if (s) { await api("/api/push/unsubscribe", { method: "POST", body: JSON.stringify({ endpoint: s.endpoint }) }); await s.unsubscribe(); }
      toast("Push disabled", "ok"); renderPush();
    } catch (e) { toast(e.message, "err"); }
  };
  bTest.onclick = async () => {
    try { const r = await api("/api/push/test", { method: "POST" }); toast(`Test sent to ${r.sent} device(s)`, "ok"); }
    catch (e) { toast(e.message, "err"); }
  };
}

// ---- login -------------------------------------------------------------
function renderLogin() {
  document.body.classList.add("auth-locked");
  view.innerHTML = `<div class="login-wrap"><form class="card login" id="loginForm">
      <div class="login-logo"><span class="logo">◉</span> PiEYE</div>
      <div class="field"><label>Username</label><input id="li_user" autocomplete="username" autofocus></div>
      <div class="field"><label>Password</label><input id="li_pass" type="password" autocomplete="current-password"></div>
      <div class="login-err" id="li_err"></div>
      <button class="btn-primary" type="submit" style="width:100%">Sign in</button>
    </form></div>`;
  document.getElementById("loginForm").onsubmit = async (e) => {
    e.preventDefault();
    const body = JSON.stringify({ username: document.getElementById("li_user").value,
                                  password: document.getElementById("li_pass").value });
    try {
      const r = await fetch("/api/login", { method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" }, body });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || "Sign-in failed");
      location.reload();
    } catch (err) { document.getElementById("li_err").textContent = err.message; }
  };
}

// ---- modal / router ----------------------------------------------------
function showModal() { document.getElementById("modal").hidden = false; }
function closeModal() { document.getElementById("modal").hidden = true; }
document.getElementById("modalClose").onclick = closeModal;
document.getElementById("modal").onclick = e => { if (e.target.id === "modal") closeModal(); };

function escapeHtml(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

const ROUTES = { live: renderLive, events: renderEvents, settings: renderSettings };
function route() {
  const tab = (location.hash.replace("#", "") || "live");
  document.querySelectorAll(".tabbar a").forEach(a => a.classList.toggle("active", a.dataset.tab === tab));
  (ROUTES[tab] || renderLive)();
}
window.addEventListener("hashchange", route);

// ---- boot --------------------------------------------------------------
async function boot() {
  let me;
  try { me = await fetch("/api/me", { credentials: "same-origin" }).then(r => r.json()); }
  catch { me = { auth_required: false, authenticated: true }; }
  if (me.auth_required && !me.authenticated) { renderLogin(); return; }
  document.body.classList.remove("auth-locked");
  route();
  pollHealth();
  setInterval(pollHealth, 5000);
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
}
boot();
