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
  const arm = document.getElementById("armBtn");
  try {
    state.health = await api("/api/health");
    const s = state.health.status;
    dot.className = "dot " + (s.error ? "err" : s.running ? "ok" : "");
    txt.textContent = s.error ? "error" : s.running ? `${s.cameras.length} cam · ${s.backend}${s.claude ? " · claude" : ""}` : "idle";
    arm.hidden = false;
    arm.className = "arm-btn " + (s.armed ? "on" : "off");
    arm.textContent = s.armed ? "ARMED" : "DISARMED";
    arm.title = s.armed ? "Alerts on — tap to disarm" : "Alerts off — tap to arm";
    arm.onclick = async () => {
      arm.disabled = true;
      try {
        const r = await api("/api/arm", { method: "POST", body: JSON.stringify({ armed: !s.armed }) });
        toast(r.armed ? "Armed — alerts on" : "Disarmed — alerts off", "ok");
      } catch (e) { toast(e.message, "err"); }
      arm.disabled = false;
      pollHealth();
    };
  } catch (e) {
    dot.className = "dot err"; txt.textContent = "offline";
  }
}

// ---- LIVE --------------------------------------------------------------
async function renderLive() {
  clearLive();
  await pollHealth();
  const cams = (state.health && state.health.status.cameras) || [];
  const failed = (state.health && state.health.status.failed_cameras) || {};
  if (!cams.length && !Object.keys(failed).length) {
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
  Object.entries(failed).forEach(([cam, err]) => {
    grid.appendChild(el(`<div class="card cam-card">
      <div class="cam-down"><div class="big">⚠</div><div>camera unavailable</div></div>
      <div class="meta"><span class="name">${escapeHtml(cam)}</span>
        <span class="badge down">OFFLINE</span>
        <div class="msg">${escapeHtml(err)}</div></div>
    </div>`));
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
        <span class="badge">${ev.camera}</span>${ev.clip ? ` <span class="badge clip">▶ CLIP</span>` : ""}
        <div class="msg">${escapeHtml(ev.message)}</div>
        <div class="time">${escapeHtml(ev.labels)} · ${fmtTime(ev.iso)}</div>
      </div></div>`);
    card.onclick = () => openEvent(ev);
    grid.appendChild(card);
  });
}
function clipUrl(file) { return `/api/clips/${encodeURIComponent(file)}`; }

function openEvent(ev) {
  const c = document.getElementById("modalContent");
  // Prefer the clip when one was recorded; fall back to the still.
  const media = ev.clip
    ? `<video controls autoplay muted playsinline preload="metadata"
              poster="${ev.snapshot ? snapUrl(ev.snapshot) : ""}"
              src="${clipUrl(ev.clip)}" style="width:100%;display:block;background:#000"></video>`
    : (ev.snapshot ? `<img src="${snapUrl(ev.snapshot)}">` : "");
  c.innerHTML = `${media}
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

    <div class="section-title">Arming schedule</div>
    <div class="card" style="padding:14px">
      <div class="field inline"><input type="checkbox" id="a_sched" ${cfg.arming.schedule_enabled ? "checked" : ""}><label style="margin:0">Arm and disarm automatically</label></div>
      <div class="row">
        <div class="field"><label>Arm at</label><input type="time" id="a_on" value="${cfg.arming.arm_at || "22:00"}"></div>
        <div class="field"><label>Disarm at</label><input type="time" id="a_off" value="${cfg.arming.disarm_at || "07:00"}"></div>
      </div>
      <div class="hint">Overnight windows are fine (e.g. 22:00 → 07:00). Tapping the
        ARMED/DISARMED button overrides until the next scheduled change.</div>
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
      <div class="field inline"><input type="checkbox" id="s_clips" ${(cfg.storage.clips || {}).enabled ? "checked" : ""}><label style="margin:0">Record a video clip for each event</label></div>
      <div class="row">
        <div class="field"><label>Seconds before</label><input type="number" id="s_pre" value="${(cfg.storage.clips || {}).pre_seconds ?? 4}"></div>
        <div class="field"><label>Seconds after</label><input type="number" id="s_post" value="${(cfg.storage.clips || {}).post_seconds ?? 6}"></div>
        <div class="field"><label>Clip FPS</label><input type="number" id="s_cfps" value="${(cfg.storage.clips || {}).fps ?? 8}"></div>
      </div>
      <div class="hint">Clips use more SD-card space — keep retention modest if enabled.</div>
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
    const pts = Array.isArray(c.zone) ? c.zone.length : 0;
    const row = el(`<div class="cam-row">
      <input value="${c.id}" placeholder="name" data-i="${i}" data-k="id">
      <input value="${c.source}" placeholder="index or rtsp url" data-i="${i}" data-k="source">
      <select data-i="${i}" data-k="rotate">${[0,90,180,270].map(r => `<option ${c.rotate==r?"selected":""}>${r}</option>`).join("")}</select>
      <button class="btn-ghost zone" data-i="${i}">${pts >= 3 ? `Zone (${pts})` : "Set zone"}</button>
      <button class="btn-danger del" data-i="${i}">✕</button></div>`);
    wrap.appendChild(row);
  });
  wrap.querySelectorAll(".zone").forEach(b => b.onclick = e =>
    openZoneEditor(parseInt(e.target.dataset.i)));
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
  cfg.arming = cfg.arming || {};
  cfg.arming.schedule_enabled = document.getElementById("a_sched").checked;
  cfg.arming.arm_at = document.getElementById("a_on").value || "22:00";
  cfg.arming.disarm_at = document.getElementById("a_off").value || "07:00";
  cfg.notify.ntfy_enabled = document.getElementById("n_en").checked;
  cfg.notify.ntfy_server = document.getElementById("n_srv").value.trim();
  cfg.notify.ntfy_topic = document.getElementById("n_topic").value.trim();
  cfg.notify.priority = document.getElementById("n_pri").value;
  cfg.notify.min_confidence_to_alert = num("n_min");
  cfg.claude.enabled = document.getElementById("c_en").checked;
  cfg.claude.model = document.getElementById("c_model").value.trim();
  cfg.storage.retention_days = num("s_ret"); cfg.storage.max_events = num("s_max");
  cfg.storage.clips = Object.assign({}, cfg.storage.clips, {
    enabled: document.getElementById("s_clips").checked,
    pre_seconds: num("s_pre"), post_seconds: num("s_post"), fps: num("s_cfps"),
  });
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

// ---- detection zone editor --------------------------------------------
// Points are stored normalized (0..1) so a zone survives resolution changes.
function openZoneEditor(idx) {
  const cam = state.cfg.cameras[idx];
  let pts = Array.isArray(cam.zone) ? cam.zone.map(p => [+p[0], +p[1]]) : [];

  document.getElementById("modalContent").innerHTML = `
    <div class="detail">
      <h3>Detection zone — ${escapeHtml(cam.id)}</h3>
      <div class="kv">Tap to add points. Only motion <strong>inside</strong> the shape
        triggers alerts. Fewer than 3 points = watch the whole frame.</div>
    </div>
    <div class="zone-wrap" id="zoneWrap">
      <img id="zoneImg" alt="" src="/api/cameras/${encodeURIComponent(cam.id)}/live.jpg?ts=${Date.now()}">
      <canvas id="zoneCanvas"></canvas>
    </div>
    <div class="detail">
      <div class="kv" id="zoneInfo"></div>
      <div class="actions" style="position:static">
        <button class="btn-ghost" id="zUndo">Undo</button>
        <button class="btn-ghost" id="zClear">Clear</button>
        <button class="btn-primary" id="zSave">Use zone</button>
      </div>
    </div>`;
  showModal();

  const img = document.getElementById("zoneImg");
  const cv = document.getElementById("zoneCanvas");
  const info = document.getElementById("zoneInfo");
  const ctx = cv.getContext("2d");

  const fit = () => { cv.width = img.clientWidth; cv.height = img.clientHeight; draw(); };
  function draw() {
    ctx.clearRect(0, 0, cv.width, cv.height);
    info.textContent = pts.length >= 3
      ? `${pts.length} points — zone active`
      : `${pts.length} point(s) — need at least 3 (currently whole frame)`;
    if (!pts.length) return;
    const P = pts.map(([x, y]) => [x * cv.width, y * cv.height]);
    ctx.beginPath();
    ctx.moveTo(P[0][0], P[0][1]);
    P.slice(1).forEach(p => ctx.lineTo(p[0], p[1]));
    if (P.length >= 3) ctx.closePath();
    ctx.fillStyle = "rgba(76,141,255,.25)";
    ctx.strokeStyle = "#4c8dff";
    ctx.lineWidth = 2;
    if (P.length >= 3) ctx.fill();
    ctx.stroke();
    P.forEach(([x, y], i) => {
      ctx.beginPath(); ctx.arc(x, y, 6, 0, Math.PI * 2);
      ctx.fillStyle = i === 0 ? "#35c28f" : "#4c8dff"; ctx.fill();
      ctx.strokeStyle = "#0f1216"; ctx.lineWidth = 2; ctx.stroke();
    });
  }
  cv.onclick = (e) => {
    const r = cv.getBoundingClientRect();
    pts.push([ (e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height ]);
    draw();
  };
  document.getElementById("zUndo").onclick = () => { pts.pop(); draw(); };
  document.getElementById("zClear").onclick = () => { pts = []; draw(); };
  document.getElementById("zSave").onclick = () => {
    if (pts.length && pts.length < 3) { toast("Need at least 3 points (or Clear)", "err"); return; }
    // round to 4dp so config.yaml stays readable
    if (pts.length >= 3) cam.zone = pts.map(([x, y]) => [+x.toFixed(4), +y.toFixed(4)]);
    else delete cam.zone;
    closeModal(); renderCamRows();
    toast("Zone set — press Save & apply to activate", "ok");
  };

  if (img.complete && img.naturalWidth) fit();
  img.onload = fit;
  img.onerror = () => { info.textContent = "No live frame available for this camera."; fit(); };
  window.addEventListener("resize", fit, { once: true });
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
    // Self-heal: this device may hold a subscription the server never stored
    // (interrupted enable, or the events DB was reset). Re-sending is an upsert.
    try {
      const r = await api("/api/push/subscribe", { method: "POST", body: JSON.stringify(sub.toJSON()) });
      st.subscriptions = r.subscriptions;
    } catch { /* keep whatever status we already have */ }
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
      // A subscription bound to older VAPID keys can't be reused -- drop it first.
      const existing = await r.pushManager.getSubscription();
      if (existing) await existing.unsubscribe();
      const s = await r.pushManager.subscribe({
        userVisibleOnly: true, applicationServerKey: b64ToUint8(public_key) });
      const res = await api("/api/push/subscribe", { method: "POST", body: JSON.stringify(s.toJSON()) });
      toast(`Push enabled (${res.subscriptions} device(s))`, "ok"); renderPush();
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
    try {
      const r = await api("/api/push/test", { method: "POST" });
      if (r.sent > 0) { toast(`Test sent to ${r.sent} device(s)`, "ok"); return; }
      toast(r.subscriptions ? "Push service rejected all devices — check the Pi's logs"
                            : "No devices registered — press Disable then Enable", "err");
      renderPush();
    } catch (e) { toast(e.message, "err"); }
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
  if ("serviceWorker" in navigator) {
    // Reload when a NEW worker replaces an existing one (i.e. the app was updated
    // on the Pi). Skip it on first-ever install: nothing stale is running, and
    // reloading there would abort in-flight work such as push registration.
    const hadController = !!navigator.serviceWorker.controller;
    let reloading = false;
    navigator.serviceWorker.addEventListener("controllerchange", () => {
      if (!hadController || reloading) return;
      reloading = true;
      location.reload();
    });
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }
}
boot();
