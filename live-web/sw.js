// Footmo private-trial app shell only. Scientific data and authenticated API
// responses are ALWAYS network-only: never store match predictions or results.
const SHELL_CACHE = 'footmo-shell-v1';
const OFFLINE_PAGE = '/offline.html';
const SHELL_ASSETS = [
  OFFLINE_PAGE, '/styles.css', '/app.js', '/manifest.webmanifest',
  '/icons/footmo-192.png', '/icons/footmo-512.png'
];
self.addEventListener('install', event => {
  event.waitUntil((async () => {
    const cache = await caches.open(SHELL_CACHE);
    await Promise.allSettled(SHELL_ASSETS.map(async path => {
      const response = await fetch(path, { cache: 'no-store', credentials: 'same-origin' });
      if (response.ok && !response.redirected &&
          new URL(response.url).origin === self.location.origin) {
        await cache.put(path, response);
      }
    }));
    await self.skipWaiting();
  })());
});
self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names.filter(name => name.startsWith('footmo-shell-') &&
      name !== SHELL_CACHE).map(name => caches.delete(name)));
    await self.clients.claim();
  })());
});
self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  // Explicitly never intercept, persist, or fall back to stale private data.
  if (url.pathname.startsWith('/api/')) return;
  if (request.mode === 'navigate') {
    event.respondWith(fetch(request).catch(async () =>
      (await caches.match(OFFLINE_PAGE)) || new Response(
        'Footmo is offline. Connect to the internet to see verified predictions.',
        { status: 503, headers: { 'Content-Type': 'text/plain; charset=utf-8' } }
      )));
    return;
  }
  if (!SHELL_ASSETS.includes(url.pathname)) return;
  event.respondWith((async () => {
    try {
      const response = await fetch(request);
      if (response.ok && !response.redirected &&
          new URL(response.url).origin === self.location.origin) {
        const cache = await caches.open(SHELL_CACHE);
        await cache.put(url.pathname, response.clone());
      }
      return response;
    } catch (error) {
      const cached = await caches.match(url.pathname);
      if (cached) return cached;
      throw error;
    }
  })());
});
