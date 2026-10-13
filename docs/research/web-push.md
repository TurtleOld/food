# Web Push для Сводок в PWA

Исследование к тикету `.scratch/stage-3/issues/03-web-push.md`. Дата: 2026-10-13.

Вопрос: что нужно, чтобы Сводки приходили Web Push'ем в существующее PWA (`core/pwa.py`, `templates/service-worker.js`): VAPID-ключи, библиотека на Python, хранение подписок на Члена семьи, обработчики `push` и `notificationclick`, ограничения платформ и судьба протухших подписок.

## Коротко

- Отдельный нативный канал не нужен. Везде работает один стандарт: Push API, Notifications API и service worker, сервер подписывает запросы VAPID-ключом (RFC 8292) и шифрует payload (RFC 8291, `aes128gcm`). Apple Developer Program не нужен [A1].
- На Python берём `pywebpush` 2.5.0 (2026-08-30, Python >= 3.10). Он шифрует, подписывает VAPID и отправляет через `requests` (есть и `webpush_async` на `aiohttp`). На каждый ответ со статусом > 202 бросает `WebPushException` со `status_code` [P1][P2].
- Подписка — это строка `endpoint` и ключи `p256dh`/`auth`. Храним их в модели, привязанной к Члену семьи (FK на пользователя, у одного человека может быть несколько устройств). На 404 или 410 от push-сервиса подписку удаляем [W3][A1].
- В service worker добавляются обработчики `push` (каждый push обязан показать уведомление) и `notificationclick` (сфокусировать открытое окно или открыть нужный URL) [W1][M2].
- iOS/iPadOS 16.4+ поддерживает push только в PWA, добавленном на экран «Домой», с `display: standalone` или `fullscreen`. Наш манифест подходит. Разрешение запрашивается только по жесту пользователя. Если push пришёл без показанного уведомления, Safari отзывает подписку [A2][A1][W1].
- Android Chrome и десктопные Chrome, Edge, Firefox и Safari (macOS 13+) поддерживают Web Push и в обычной вкладке, без установки [M1].

## Что есть в проекте сейчас

- `core/views.py: ServiceWorkerView` рендерит `templates/service-worker.js` с корня (`Service-Worker-Allowed: /`, `Cache-Control: no-cache`). В воркере есть только `install`, `activate` и `fetch`.
- `static/manifest.webmanifest`: `"display": "standalone"`, `"scope": "/"`, `"start_url": "/"`. Поля `id` нет.
- `templates/base.html` регистрирует воркер, если `"serviceWorker" in navigator`.
- Python-зависимостей для push нет. Фоновых задач тоже нет (их разбирает тикет 02).

## 1. VAPID-ключи

- VAPID — это пара ключей P-256 (ECDSA). Публичный ключ передаётся в `PushManager.subscribe({applicationServerKey})`, приватный подписывает JWT в заголовке `Authorization` каждого запроса к push-сервису [A1][W2].
- Ключ, который сервер шлёт в `Authorization`, должен совпадать с ключом из `subscribe()`. Иначе Apple отвечает `403 VapidPkHashMismatch` [A1]. **Если сменить пару ключей, все существующие подписки станут бесполезны**: py-vapid прямо предупреждает, что после регенерации endpoint'ы придётся получать заново [P3]. Значит, ключи генерируются один раз на окружение и лежат в секретах (`.env`), как `SECRET_KEY`.
- Требования Apple к JWT: `sub` — URL или `mailto:`, `aud` — origin push-сервиса, `exp` не дальше суток вперёд. Обновлять JWT чаще раза в час не нужно [A1]. `pywebpush` сам подставляет `aud` из endpoint, а `exp` ставит на +12 часов, если его нет. От нас нужен только `{"sub": "mailto:…"}` [P1].
- Генерация ключей:
  - `vapid --gen` из py-vapid (ставится зависимостью pywebpush) создаёт `private_key.pem` и `public_key.pem`. `vapid --applicationServerKey` печатает публичный ключ в base64url для клиента [P3].
  - Либо `openssl ecparam -name prime256v1 -genkey -noout -out private_key.pem` [P1].
  - `vapid_private_key` в pywebpush принимает путь к PEM или DER в base64-строке [P1]. Для `.env` удобнее строка.
- Публичный ключ не секретен, его можно отдавать в шаблон (например, `data-`-атрибутом). На клиенте его переводят в `Uint8Array` (`urlBase64ToUint8Array`) [W2].

## 2. Библиотека на Python

| Пакет | Последний релиз | Что это | Вывод |
| --- | --- | --- | --- |
| `pywebpush` | 2.5.0, 2026-08-30 | Референсная библиотека из web-push-libs: шифрование, VAPID, отправка. Зависит от `requests`, `aiohttp`, `cryptography>=47`, `http-ece`, `py-vapid` [P2] | **Берём** |
| `django-webpush` | 0.3.6, 2024-04-30 | Обёртка над `pywebpush>=1.14` со своими моделями, JS и шаблонами [P4] | Не берём: два года без релизов, свои модели и JS не ложатся на наш глоссарий и ADR-0003 |
| `webpush` (delvinru/webpush-py) | 1.0.6, 2025-10-29 | Альтернатива на pydantic и pyjwt без HTTP-клиента: формирует запрос, отправляет его вызывающий код [P5] | Запасной вариант, если не нужны `requests` и `aiohttp` |

Как пользоваться `pywebpush` [P1][P6]:

```python
from pywebpush import WebPushException, webpush

try:
    webpush(
        subscription_info={"endpoint": s.endpoint, "keys": {"p256dh": s.p256dh, "auth": s.auth}},
        data=json.dumps(payload),
        vapid_private_key=settings.VAPID_PRIVATE_KEY,
        vapid_claims={"sub": settings.VAPID_SUBJECT},  # передавать копию: dict мутируется
        ttl=12 * 3600,
        headers={"Urgency": "normal", "Topic": "summary"},
        timeout=10,
    )
except WebPushException as exc:
    if exc.response is not None and exc.response.status_code in (404, 410):
        s.delete()
```

Нюансы:

- По умолчанию `ttl=0`: если устройство офлайн, push-сервис сразу выбрасывает сообщение [P6]. Для Сводки нужен ненулевой TTL, иначе выключенный телефон её не получит. Apple хранит сообщение не дольше 30 дней и ограничивает число отложенных [A1].
- `vapid_claims` мутируется на месте [P1], поэтому на каждый вызов передаём новый dict.
- `content_encoding` по умолчанию `aes128gcm`. Старый `aesgcm` объявлен устаревшим [P1].
- Отправка синхронная, это HTTP-запрос к внешнему сервису. Слать push из обработчика запроса пользователя не стоит, это работа для фоновых задач (тикет 02).
- Сервер должен иметь исходящий доступ к push-сервисам. Endpoint приходит от браузера, а не задаётся нами: `fcm.googleapis.com` у Chrome и Edge, `*.push.apple.com` у Safari [A1][W1], у Firefox свой сервис Mozilla (хост по первоисточнику не проверялся). Если в проде есть egress-фильтр, их надо разрешить. Apple отдельно требует TLS с SNI [A1].

## 3. Хранение подписок на Члена семьи

`PushSubscription.toJSON()` отдаёт `endpoint`, `expirationTime` и `keys: {p256dh, auth}` [W2]. Минимальная модель:

- `member` — FK на пользователя (Член семьи). У одного человека бывает несколько подписок: телефон, планшет, ноутбук.
- `endpoint` — `URLField`/`TextField`, **уникальный**. По нему находим запись при повторной подписке и удаляем её на 404/410. Endpoint бывает длинным, поэтому `max_length` нужен с запасом или `TextField`.
- `p256dh`, `auth` — строки в base64url.
- `created_at` и, по желанию, `user_agent` для страницы «мои устройства».

Поток данных:

- **Подписка.** Кнопка «Включать уведомления» → `Notification.requestPermission()` и `registration.pushManager.subscribe({userVisibleOnly: true, applicationServerKey})` прямо в обработчике жеста [A1][W1] → POST подписки на сервер → `update_or_create` по `endpoint` с текущим Членом семьи. Повторный POST с того же устройства обновляет запись, а не плодит дубли.
- **Отписка.** `subscription.unsubscribe()` на клиенте и удаление записи на сервере.
- **Выход из аккаунта.** На общем семейном устройстве стоит удалять подписку при logout, иначе Сводки одного Члена семьи будут приходить на устройство, где уже вошёл другой. Это решение продукта, а не ограничение платформы.
- **Расхождение с ADR-0003.** `pushManager.subscribe()` есть только в JS, поэтому нужен маленький JS-модуль (по образцу `static/js/scanner.js`). Он может отправить подписку обычной формой (`application/x-www-form-urlencoded` с CSRF-токеном) или через `htmx.ajax`, а сервер ответит HTML-фрагментом статуса. Тогда JSON-эндпоинт не нужен и ADR-0003 не нарушается.

## 4. Service worker: `push` и `notificationclick`

Обработчики добавляются в `templates/service-worker.js`. Кэширование они не трогают.

```js
self.addEventListener("push", (event) => {
  const data = event.data ? event.data.json() : {};
  const notification = data.notification || {};
  event.waitUntil(
    self.registration.showNotification(notification.title || "КБЖУ", {
      body: notification.body,
      tag: notification.tag,            // одна Сводка вытесняет предыдущую
      icon: "/static/icons/icon-192.png",
      data: { url: notification.navigate || "/" },
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = new URL(event.notification.data.url, self.location.origin).href;
  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      for (const client of list) {
        if (client.url === url && "focus" in client) return client.focus();
      }
      return clients.openWindow(url);
    })
  );
});
```

- **Каждый push обязан закончиться `showNotification`** внутри `event.waitUntil`. Это обещание `userVisibleOnly: true`. Chrome без этого флага не даёт подписаться вообще [W2]. Safari отзывает разрешение, если уведомление не показано [A1][W1]. Значит, «тихих» push для синхронизации данных не будет: каждый push — это видимая Сводка.
- Схема `notificationclick` взята из MDN [M2]. `clients.openWindow` и `WindowClient.focus` работают в Safari iOS с 11.3 [M1].
- Payload лучше сразу собирать в формате Declarative Web Push: `{"web_push": 8030, "notification": {"title": …, "body": …, "navigate": "/…"}}`. Safari на iOS/iPadOS 18.4+ показывает такое уведомление сам, даже без service worker, а старые браузеры обрабатывают тот же JSON нашим `push`-обработчиком. Совместимость в обе стороны, и нет штрафа, если воркер не успел показать уведомление [W4]. Обработчик выше уже читает этот формат.
- Максимальный размер payload — 4 КБ. Apple отвечает `413 PayloadTooLarge` [A1], и по спецификации гарантированы те же 4096 байт [W3]. Сводка должна быть коротким текстом со ссылкой, а не полным отчётом.
- `pushsubscriptionchange` (браузер сам сменил подписку) поддерживают Chrome 138+, Firefox (без `oldSubscription`/`newSubscription`) и Safari на macOS. В Safari iOS его нет [M1]. Полагаться на это событие нельзя. Надёжнее при каждом открытии приложения, если разрешение выдано, вызывать `pushManager.getSubscription()` и пересылать подписку на сервер (идемпотентный `update_or_create`).

## 5. Ограничения платформ

### iOS / iPadOS

- Нужна версия 16.4 или новее. Push работает **только в веб-приложении, добавленном на экран «Домой»**, в обычной вкладке Safari его нет. В манифесте должно быть `display: standalone` или `fullscreen`, иначе сайт сохраняется как закладка [A2][A1]. У нас `standalone`, подходит.
- Вне установленного PWA объект `Notification` вообще не определён [M1]. Feature detection (`"PushManager" in window`, `"Notification" in window`) позволит показать в Safari подсказку «добавьте на экран „Домой“» вместо кнопки подписки. WebKit сам советует feature detection, а не определение браузера [A2].
- Разрешение запрашивается только по прямому жесту (тап по кнопке), причём `subscribe()` вызывается сразу из обработчика этого жеста [A1][A2].
- Push идёт через APNs (`*.push.apple.com`). Уведомления попадают на экран блокировки, в Центр уведомлений и на Apple Watch, учитывают режимы фокусирования [A2].
- Бейдж иконки (`navigator.setAppBadge`) работает с 16.4 в установленном PWA и виден после разрешения уведомлений [A2][M1]. Его можно ставить из `push`-обработчика, например «есть непрочитанная Сводка».
- Манифест `id` вместе с именем идентифицирует приложение и синхронизирует настройки фокусирования между устройствами [A2]. Сейчас `id` в нашем манифесте нет. Его стоит добавить заранее (`"id": "/"`): смена `id` потом сделает приложение «другим».
- Declarative Web Push работает с iOS/iPadOS 18.4 [W4].
- Не проверено по первоисточнику: состояние PWA на iOS в ЕС после DMA (iOS 17.4). Страница Apple о DMA про веб-приложения на экране «Домой» ничего не говорит [A3]. Для семьи вне ЕС это не важно.

### Android (Chrome)

- Push API работает с Chrome 42, `push`/`notificationclick` с Chrome 40/42 [M1]. Установка PWA **не нужна**, push приходит и в обычную вкладку.
- `userVisibleOnly: true` обязателен [W2].
- С октября 2025 Chrome на Android и десктопе автоматически отзывает разрешение на уведомления у сайтов с очень низкой вовлечённостью и большим потоком уведомлений. **Установленные веб-приложения не затрагиваются** [G1]. Значит, Членам семьи стоит ставить PWA и на Android.
- `setAppBadge` в Chrome Android не поддерживается [M1].

### Десктоп

- Chrome и Edge (с 17) [M1], Firefox (с 44) [M1], Safari 16+ на macOS 13 Ventura+ [A1][M1]. Везде достаточно обычной вкладки, хотя у Chrome автоотзыв, как на Android, не трогает установленные приложения [G1].
- Firefox допускает подписку без `applicationServerKey`, но мы всё равно передаём VAPID-ключ, потому что Chrome без него не работает [W2].

## 6. Протухшие подписки

- Подписка может умереть без нашего участия: пользователь отозвал разрешение, удалил PWA с экрана «Домой», почистил данные сайта, браузер обновил подписку, Safari отозвал её за невидимый push, Chrome отозвал разрешение у малоактивного сайта [W1][G1][M3].
- Push-сервис в таком случае отвечает:
  - **410 Gone** — подписка недействительна, **удалить** [W3][A1]. Apple формулирует это как «The device token has expired» [A1].
  - **404 Not Found** — подписки нет, **удалить** [W3].
  - **413** — уменьшить payload. **429** — подождать `Retry-After` (у `pywebpush` есть `exc.retry_after`). **400** — исправить запрос. Во всех трёх случаях подписку **не удалять** [W3][P1].
  - **403** — ошибка аутентификации, например `VapidPkHashMismatch` или `BadJwtToken` у Apple. Это проблема нашего ключа или JWT, а не подписки, удалять её не надо [A1].
- Поле `expirationTime` есть во всех браузерах [M1], но время жизни подписки оно не гарантирует (подписку могут отозвать раньше). Хранить его можно, опираться на него не стоит. Главные сигналы — 404/410 при отправке плюс переотправка `getSubscription()` при открытии приложения (раздел 4).
- Следствие для UI: «уведомления включены» — это не флаг в профиле, а «у Члена семьи есть хотя бы одна живая подписка». Если все подписки удалены по 410, приложение при следующем открытии покажет кнопку подписки снова.

## Открытые вопросы для реализации

1. Где отправлять: синхронно или в фоновой задаче. Зависит от тикета 02 (фоновые задачи), а расписание Сводок — от тикета 08.
2. Удалять ли подписку при выходе из аккаунта на общем устройстве (раздел 3).
3. Нужен ли ответный канал из уведомления, то есть `actions` в уведомлении (поддержка по платформам неоднородна, здесь не исследовалась).

## Источники

- [A1] Apple Developer Documentation, «Sending web push notifications in web apps and browsers»: https://developer.apple.com/documentation/usernotifications/sending-web-push-notifications-in-web-apps-and-browsers (прочитано через JSON-представление `developer.apple.com/tutorials/data/documentation/usernotifications/sending-web-push-notifications-in-web-apps-and-browsers.json`)
- [A2] WebKit Blog, «Web Push for Web Apps on iOS and iPadOS» (iOS 16.4): https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/
- [A3] Apple Developer, «DMA and apps in the EU»: https://developer.apple.com/support/dma-and-apps-in-the-eu/
- [W1] WebKit Blog, «Meet Web Push»: https://webkit.org/blog/12945/meet-web-push/
- [W2] web.dev, «Subscribing a user»: https://web.dev/articles/push-notifications-subscribing-a-user
- [W3] web.dev, «Common issues and reporting bugs» (коды ответа push-сервисов): https://web.dev/articles/push-notifications-common-issues-and-reporting-bugs
- [W4] WebKit Blog, «Meet Declarative Web Push»: https://webkit.org/blog/16535/meet-declarative-web-push/
- [M1] MDN browser-compat-data 8.1.5 (2026-10-08): `api.PushManager`, `api.ServiceWorkerGlobalScope.{push_event,notificationclick_event,pushsubscriptionchange_event}`, `api.ServiceWorkerRegistration.showNotification`, `api.Notification.requestPermission_static`, `api.Clients.openWindow`, `api.WindowClient.focus`, `api.Navigator.setAppBadge`, `api.PushSubscription.expirationTime`
- [M2] MDN, «ServiceWorkerGlobalScope: notificationclick event»: https://developer.mozilla.org/en-US/docs/Web/API/ServiceWorkerGlobalScope/notificationclick_event
- [M3] MDN, «ServiceWorkerGlobalScope: pushsubscriptionchange event»: https://developer.mozilla.org/en-US/docs/Web/API/ServiceWorkerGlobalScope/pushsubscriptionchange_event
- [G1] Google Chromium blog, «Reducing notification overload for a quieter browsing experience in Chrome» (2025-10-10): https://blog.google/chromium/automatic-notification-permission/
- [P1] pywebpush README: https://github.com/web-push-libs/pywebpush
- [P2] PyPI `pywebpush` (2.5.0, requires-python >= 3.10, зависимости): https://pypi.org/project/pywebpush/
- [P3] PyPI `py-vapid` 1.9.4 (CLI `vapid --gen`, `--applicationServerKey`): https://pypi.org/project/py-vapid/
- [P4] PyPI `django-webpush` 0.3.6: https://pypi.org/project/django-webpush/
- [P5] PyPI `webpush` 1.0.6: https://pypi.org/project/webpush/
- [P6] Исходник `pywebpush/__init__.py` (сигнатуры `webpush`/`webpush_async`, `ttl=0` по умолчанию, исключение на статус > 202): https://github.com/web-push-libs/pywebpush/blob/main/pywebpush/__init__.py
