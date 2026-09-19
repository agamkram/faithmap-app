/* FaithMap service worker — production offline shell.
   Local HTTPS unregisters this (see index.html IS_LOCAL).
   Bump CACHE with APP_VERSION / ?v= via scripts/bump-version.py. */
const CACHE = "faithmap-v76";
const PRECACHE = [
  "/",
  "/index.html?v=76",
  "/styles.css?v=76",
  "/app.js?v=76",
  "/manifest.webmanifest",
  "/icon-192.png",
  "/icon-512.png",
  "/icon-maskable-512.png",
  "/apple-touch-icon.png",
  "/favicon-32.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then(async (c) => {
        await Promise.all(PRECACHE.map((u) => c.add(u).catch(() => {})));
      })
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) {
    event.respondWith(fetch(req).catch(() => caches.match(req)));
    return;
  }

  const path = url.pathname;
  /* Vercel Web Analytics — do not cache or intercept. */
  if (path.startsWith("/_vercel/")) return;

  const isNav = req.mode === "navigate" || path === "/" || path.endsWith(".html");
  const isCode =
    path.endsWith(".js") ||
    path.endsWith(".css") ||
    path.endsWith("manifest.webmanifest");

  if (isNav || isCode) {
    event.respondWith(
      fetch(req)
        .then((res) => {
          const copy = res.clone();
          caches.open(CACHE).then((cache) => cache.put(req, copy)).catch(() => {});
          return res;
        })
        .catch(() => caches.match(req).then((hit) => hit || caches.match("/")))
    );
    return;
  }

  event.respondWith(
    caches.match(req).then((hit) => {
      if (hit) return hit;
      return fetch(req).then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((cache) => cache.put(req, copy)).catch(() => {});
        return res;
      });
    })
  );
});
