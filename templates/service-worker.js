{% autoescape off %}const CACHE_NAME = "{{ cache_name }}";
const PRECACHE_URLS = {{ precache_json }};
const OFFLINE_URL = "{{ offline_url }}";
const STATIC_PREFIX = "{{ static_prefix }}";
const BYPASS_CACHE = {% if bypass_cache %}true{% else %}false{% endif %};

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) => cache.addAll(PRECACHE_URLS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

// Каждый push обязан показать уведомление: иначе Safari отзывает подписку.
// Один и тот же JSON понимает Declarative Web Push (web_push: 8030) и этот обработчик.
self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = {};
  }
  const notification = data.notification || {};
  event.waitUntil(
    self.registration.showNotification(notification.title || "КБЖУ", {
      body: notification.body || "",
      tag: notification.tag || "summary",
      icon: "{{ notification_icon }}",
      data: { url: notification.navigate || "/" },
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  let target;
  try {
    target = new URL(event.notification.data.url || "/", self.location.origin);
  } catch {
    target = new URL("/", self.location.origin);
  }
  if (target.origin !== self.location.origin) {
    target = new URL("/", self.location.origin);
  }
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((windowClients) => {
      for (const client of windowClients) {
        if (client.url === target.href && "focus" in client) return client.focus();
      }
      return self.clients.openWindow(target.href);
    })
  );
});

// Личные страницы и htmx-фрагменты никогда не кэшируются: перехватываем
// только навигацию (ради страницы «Нет сети») и статику.
self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);

  if (BYPASS_CACHE) {
    return;
  }
  if (request.method !== "GET" || request.headers.has("HX-Request") || url.origin !== self.location.origin) {
    return;
  }

  if (request.mode === "navigate") {
    event.respondWith(fetch(request).catch(() => caches.match(OFFLINE_URL)));
    return;
  }

  if (url.pathname.startsWith(STATIC_PREFIX)) {
    event.respondWith(
      caches.match(request).then(
        (cached) =>
          cached ||
          fetch(request).then((response) => {
            if (response.ok) {
              const copy = response.clone();
              caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
            }
            return response;
          })
      )
    );
  }
});
{% endautoescape %}
