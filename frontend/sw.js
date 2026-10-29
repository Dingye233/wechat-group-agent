// 问道 · 修仙界 service worker：把壳页面和两张游戏页缓存起来，离线也能进
const CACHE = 'qunban-rpg-v1';
const SHELL = ['/rpg', '/rpg/manifest.json', '/rpg/icon-192.png', '/rpg/icon-512.png'];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))),
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  // 页面和图标走「网络优先，失败回缓存」；接口一律走网络
  if (url.pathname.startsWith('/api/')) return;
  event.respondWith(
    fetch(req)
      .then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((cache) => cache.put(req, copy)).catch(() => {});
        return res;
      })
      .catch(() => caches.match(req).then((hit) => hit || caches.match('/rpg/play'))),
  );
});
