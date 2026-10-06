// Cache every asset on install so the drill works with no signal at all.
const CACHE = 'hard-sixteen-v1';
const ASSETS = [
  './', 'index.html', 'manifest.webmanifest', 'src/styles.css',
  'src/app/main.js', 'src/app/store.js', 'src/app/format.js',
  'src/engine/rules.js', 'src/engine/cards.js', 'src/engine/odds.js',
  'src/engine/strategy.js', 'src/engine/scenarios.js', 'src/engine/srs.js',
  'icons/icon.svg', 'icons/icon-192.png', 'icons/icon-512.png', 'icons/apple-touch-icon.png',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', (e) => {
  if (e.request.method !== 'GET') return;
  const url = new URL(e.request.url);
  if (url.origin !== location.origin) return;   // let the font CDN use the browser cache
  e.respondWith(
    caches.match(e.request).then((hit) => hit || fetch(e.request).then((res) => {
      const copy = res.clone();
      caches.open(CACHE).then((c) => c.put(e.request, copy)).catch(() => {});
      return res;
    }).catch(() => caches.match('index.html'))),
  );
});
