// App-shell caching for offline/installability. The shell is NETWORK-FIRST so an
// update on the Pi appears on the next load without bumping this version; the
// cache is only a fallback for when the Pi is unreachable. API responses and
// snapshots are never cached, so events and the live view are always current.
const CACHE = "pv-shell-v5";
const SHELL = ["/", "/index.html", "/styles.css", "/app.js", "/manifest.webmanifest", "/icons/icon.svg"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// ---- web push ----
self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch { d = { body: e.data && e.data.text() }; }
  const title = d.title || "PiEYE";
  const opts = {
    body: d.body || "Motion detected",
    icon: "/icons/icon.svg",
    badge: "/icons/icon.svg",
    tag: d.tag || "pieye",
    renotify: true,
    data: { url: d.url || "/#events" },
  };
  if (d.image) opts.image = d.image;   // annotated snapshot, where supported
  e.waitUntil(self.registration.showNotification(title, opts));
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || "/#events";
  e.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      for (const c of list) {
        if ("focus" in c) { c.navigate(url); return c.focus(); }
      }
      return self.clients.openWindow(url);
    })
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET") return;
  if (url.origin !== self.location.origin) return;
  // never cache dynamic data
  if (url.pathname.startsWith("/api/")) return;

  // Network-first for the app shell: a `git pull` on the Pi shows up on the next
  // load without needing a cache-version bump. The cache is only a fallback for
  // when the Pi is unreachable.
  e.respondWith(
    fetch(e.request)
      .then((res) => {
        if (res && res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy)).catch(() => {});
        }
        return res;
      })
      // Scope the offline fallback to the CURRENT cache -- a bare caches.match()
      // searches every cache and could resurrect a previous version's code.
      .catch(() => caches.open(CACHE).then((c) => c.match(e.request)))
  );
});
