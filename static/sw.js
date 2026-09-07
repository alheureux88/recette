const CACHE = "recettes-v1";
const SHELL = ["/", "/static/css/style.css", "/static/favicon.svg", "/manifest.webmanifest"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  if (url.pathname.startsWith("/admin") || url.pathname.startsWith("/auth") ||
      url.pathname.startsWith("/favorites") || url.pathname.startsWith("/recipe")) {
    return;
  }
  e.respondWith(
    fetch(req)
      .then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(req, copy));
        return res;
      })
      .catch(() => caches.match(req).then((r) => r || caches.match("/")))
  );
});

self.addEventListener("push", (e) => {
  if (!e.data) return;
  let payload;
  try {
    payload = e.data.json();
  } catch (_) {
    payload = { title: e.data.text(), body: "" };
  }
  const title = payload.title || "Timer";
  const options = {
    body: payload.body || "",
    icon: payload.icon || "/static/icon-192.png",
    badge: payload.badge || "/static/favicon-32x32.png",
    data: payload.data || {},
    tag: payload.data ? "timer-" + payload.data.recipe_id + "-" + payload.data.step_index : "timer",
    requireInteraction: true,
    vibrate: [200, 100, 200, 100, 200],
  };
  if (payload.url) {
    options.data.url = payload.url;
  }
  e.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || "/";
  e.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      for (const client of list) {
        if (client.url.includes(url) && "focus" in client) return client.focus();
      }
      if (clients.openWindow) return clients.openWindow(url);
    })
  );
});
