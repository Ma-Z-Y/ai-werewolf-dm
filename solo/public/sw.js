const CACHE = 'ai-werewolf-shell-v2';
const scope = self.registration.scope;
const offlinePage = new URL('index.html', scope).href;
const buildAssets = /* @vite-precache:start */ [] /* @vite-precache:end */;
const core = [
  scope,
  offlinePage,
  new URL('manifest.webmanifest', scope).href,
  new URL('icon.svg', scope).href,
  ...buildAssets.map((asset) => new URL(asset, scope).href),
];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(core)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin || url.pathname.includes('/__llm')) return;

  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then((response) => {
          if (response.ok) void caches.open(CACHE).then((cache) => cache.put(request, response.clone()));
          return response;
        })
        .catch(() => caches.match(request).then((cached) => cached ?? caches.match(offlinePage))),
    );
    return;
  }

  event.respondWith(
    caches.match(request).then((cached) => {
      const network = fetch(request).then((response) => {
        if (response.ok) void caches.open(CACHE).then((cache) => cache.put(request, response.clone()));
        return response;
      });
      return cached ?? network;
    }).catch(() => caches.match(offlinePage)),
  );
});
