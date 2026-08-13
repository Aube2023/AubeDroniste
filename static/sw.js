// AubePilot — service worker : page hors-ligne de secours + cache des statiques.
// Stratégie volontairement prudente : les pages HTML passent TOUJOURS par le
// réseau (jamais de contenu périmé ni de session figée) ; seul l'échec réseau
// affiche /offline. Les assets statiques et les polices sont servis du cache
// puis rafraîchis en arrière-plan (stale-while-revalidate).

const VERSION = 'aube-sw-v1';
const STATIC_CACHE = VERSION + '-static';
const FONT_CACHE = VERSION + '-fonts';
const OFFLINE_URL = '/offline';

const CORE = [
  OFFLINE_URL,
  '/static/css/style.css',
  '/static/js/app.js',
  '/static/brand/logo-mark.svg',
  '/static/brand/icon-192x192.png',
  '/static/img/bg-aube.jpg',
];

self.addEventListener('install', function (e) {
  e.waitUntil(
    caches.open(STATIC_CACHE)
      .then(function (c) { return c.addAll(CORE); })
      .then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener('activate', function (e) {
  e.waitUntil(
    caches.keys()
      .then(function (keys) {
        return Promise.all(
          keys.filter(function (k) { return k.indexOf(VERSION) !== 0; })
              .map(function (k) { return caches.delete(k); })
        );
      })
      .then(function () { return self.clients.claim(); })
  );
});

self.addEventListener('fetch', function (e) {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);

  // Navigations : réseau d'abord, page hors-ligne en secours.
  if (req.mode === 'navigate') {
    e.respondWith(
      fetch(req).catch(function () { return caches.match(OFFLINE_URL); })
    );
    return;
  }

  // Statiques même origine : cache d'abord, rafraîchi en arrière-plan.
  if (url.origin === self.location.origin && url.pathname.indexOf('/static/') === 0) {
    e.respondWith(staleWhileRevalidate(req, STATIC_CACHE));
    return;
  }

  // Google Fonts : idem, cache dédié (réponses opaques acceptées).
  if (url.hostname === 'fonts.googleapis.com' || url.hostname === 'fonts.gstatic.com') {
    e.respondWith(staleWhileRevalidate(req, FONT_CACHE));
  }
});

function staleWhileRevalidate(req, cacheName) {
  return caches.open(cacheName).then(function (cache) {
    return cache.match(req).then(function (hit) {
      const refresh = fetch(req).then(function (resp) {
        if (resp && (resp.ok || resp.type === 'opaque')) cache.put(req, resp.clone());
        return resp;
      }).catch(function () { return hit; });
      return hit || refresh;
    });
  });
}
