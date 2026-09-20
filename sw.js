/* FaithMap service worker — production offline shell.
   Local HTTPS unregisters this (see index.html IS_LOCAL).
   Bump CACHE with APP_VERSION / ?v= via scripts/bump-version.py. */
const CACHE = "faithmap-v90";
const PRECACHE = [
  "/",
  "/index.html?v=90",
  "/styles.css?v=90",
  "/app.js?v=90",
  "/vendor/maplibre-gl.js?v=90",
  "/vendor/maplibre-gl.css?v=90",
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

function cacheIfOk(req, res) {
  if (!res || !res.ok || (res.type !== "basic" && res.type !== "cors")) return res;
  try {
    const copy = res.clone();
    caches.open(CACHE).then((cache) => cache.put(req, copy)).catch(() => {});
  } catch (err) {
    /* iOS quota / large bodies — still return the network response. */
  }
  return res;
}

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

  /* Do not intercept /data or /geo. Caching ~28MB JSON blows iOS quota,
     and falling back to cached HTML makes load() think places.json is missing. */
  if (path.startsWith("/data/") || path.startsWith("/geo/")) return;

  const isNav = req.mode === "navigate" || path === "/" || path.endsWith(".html");
  const isCode =
    path.endsWith(".js") ||
    path.endsWith(".css") ||
    path.endsWith("manifest.webmanifest");

  if (isNav || isCode) {
    event.respondWith(
      fetch(req)
        .then((res) => cacheIfOk(req, res))
        .catch(() => caches.match(req).then((hit) => hit || caches.match("/")))
    );
    return;
  }

  event.respondWith(
    caches.match(req).then((hit) => {
      if (hit) return hit;
      return fetch(req).then((res) => cacheIfOk(req, res));
    })
  );
});
