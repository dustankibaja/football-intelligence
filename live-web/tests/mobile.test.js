import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { runInNewContext } from 'node:vm';

const root = resolve(import.meta.dirname, '..');
const read = path => readFileSync(resolve(root, path), 'utf8');

test('PWA manifest has a stable scope and real install icons', () => {
  const manifest = JSON.parse(read('manifest.webmanifest'));
  assert.equal(manifest.name, 'Footmo — Football Intelligence');
  assert.equal(manifest.start_url, '/');
  assert.equal(manifest.scope, '/');
  assert.equal(manifest.display, 'standalone');
  assert.equal(manifest.theme_color, '#071321');
  for (const [size, path] of [
    [192, 'icons/footmo-192.png'], [512, 'icons/footmo-512.png'],
    [180, 'icons/footmo-180.png']
  ]) {
    const png = readFileSync(resolve(root, path));
    assert.deepEqual([...png.subarray(0, 8)], [137,80,78,71,13,10,26,10]);
    assert.equal(png.readUInt32BE(16), size);
    assert.equal(png.readUInt32BE(20), size);
  }
  assert.ok(manifest.icons.some(i => i.sizes === '192x192'));
  assert.ok(manifest.icons.some(i => i.sizes === '512x512' && i.purpose.includes('maskable')));
});

test('mobile layout provides viewport-safe touch navigation and install hints', () => {
  const html = read('index.html');
  const css = read('styles.css');
  const js = read('app.js');
  assert.match(html, /viewport-fit=cover/);
  assert.match(html, /rel="manifest" href="\/manifest\.webmanifest"/);
  assert.match(html, /rel="apple-touch-icon"/);
  assert.match(html, /id="predictions"/);
  assert.match(html, /id="history"/);
  assert.match(html, /id="performance"/);
  assert.match(html, /aria-label="Mobile app navigation"/);
  assert.match(html, /id="install-app"/);
  assert.match(html, /id="connection-banner"/);
  assert.match(css, /env\(safe-area-inset-bottom/);
  assert.match(css, /min-height:44px/);
  assert.match(css, /\.mobile-nav\{position:fixed/);
  assert.match(js, /beforeinstallprompt/);
  assert.match(js, /serviceWorker\.register\('\/sw\.js'/);
  assert.match(js, /window\.addEventListener\('offline'/);
});

test('service worker never intercepts API requests or private match data', () => {
  const listeners = {};
  const self = {
    location: { origin: 'https://footmo.example' },
    addEventListener(name, callback) { listeners[name] = callback; },
    skipWaiting() { return Promise.resolve(); },
    clients: { claim() { return Promise.resolve(); } }
  };
  const caches = {
    open: async () => ({ put: async () => {} }),
    match: async () => null,
    keys: async () => [],
    delete: async () => true
  };
  runInNewContext(read('sw.js'), {
    self, caches, fetch: async () => { throw new Error('offline'); },
    Response, URL, Promise
  });
  let intercepted = false;
  const event = request => ({
    request, respondWith() { intercepted = true; }
  });
  listeners.fetch(event({ method: 'GET',
    url: 'https://footmo.example/api/state', mode: 'cors' }));
  assert.equal(intercepted, false);
  listeners.fetch(event({ method: 'GET',
    url: 'https://footmo.example/api/state?force=1', mode: 'cors' }));
  assert.equal(intercepted, false);
  listeners.fetch(event({ method: 'POST',
    url: 'https://footmo.example/api/refresh', mode: 'cors' }));
  assert.equal(intercepted, false);
  listeners.fetch(event({ method: 'GET',
    url: 'https://other.example/styles.css', mode: 'cors' }));
  assert.equal(intercepted, false);
});
