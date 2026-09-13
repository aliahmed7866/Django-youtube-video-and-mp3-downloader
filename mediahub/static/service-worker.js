'use strict';
// Never cache the queue, media, or a page containing a session CSRF token.
self.addEventListener('install', event => event.waitUntil(self.skipWaiting()));
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
self.addEventListener('fetch', event => {
  if (event.request.mode !== 'navigate') return;
  event.respondWith(fetch(event.request).catch(() => new Response(
    '<!doctype html><html lang="en"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Media Hub is offline</title><body><h1>Start Media Hub in Termux</h1><p>Your collection is still on your device. Start the Media Hub service, then refresh this page. Shared links remain in the address bar.</p><button onclick="location.reload()">Try again</button></body></html>',
    {status:503,headers:{'Content-Type':'text/html; charset=utf-8'}}
  )));
});
