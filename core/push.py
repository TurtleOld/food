"""Web Push: подписки устройств Члена семьи и отправка уведомлений по согласию.

Отправка идёт по подпискам, которые браузер оформил явным действием человека;
без подписок `send_push` ничего не делает. Протухает только подписка, на которую
push-сервис ответил 404 или 410, — остальные ошибки её не удаляют.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from django.db import transaction
from django.tasks import task
from pywebpush import WebPushException, webpush

from core.models import PushSettings, PushSubscription

logger = logging.getLogger(__name__)

# Сервисы push-уведомлений браузеров. Endpoint приходит из браузера, поэтому
# неизвестный хост не принимаем: иначе подписка стала бы запросом куда угодно.
ALLOWED_ENDPOINT_HOSTS = (
    "fcm.googleapis.com",
    "push.apple.com",
    "push.services.mozilla.com",
    "notify.windows.com",
)

# Запас под шифрование и заголовки: сам протокол гарантирует 4096 байт на
# зашифрованное сообщение, а не на наш JSON.
MAX_PAYLOAD_BYTES = 3800
DEFAULT_TTL_SECONDS = 12 * 60 * 60
REQUEST_TIMEOUT_SECONDS = 10


class PushNotConfigured(RuntimeError):
    """VAPID-ключи не заданы, поэтому отправлять push нельзя."""


class PushPayloadTooLarge(ValueError):
    """Payload не влезает в ограничение Web Push даже после сокращения."""


@dataclass(frozen=True)
class PushDelivery:
    """Итог одной рассылки: доставлено, протухло и не доставлено."""

    sent: int
    expired: int
    failed: int


def is_configured() -> bool:
    """Push включён только при полном наборе VAPID-настроек в БД."""
    return PushSettings.current().is_configured


def is_allowed_push_endpoint(endpoint: str) -> bool:
    """Разрешает только HTTPS-endpoint известного push-сервиса без учётных данных."""
    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        return False
    host = (parsed.hostname or "").lower()
    return any(
        host == allowed or host.endswith(f".{allowed}") for allowed in ALLOWED_ENDPOINT_HOSTS
    )


def subscribe(*, member_id: int, endpoint: str, p256dh: str, auth: str) -> PushSubscription:
    """Сохраняет подписку устройства; повтор с тем же endpoint обновляет её.

    Endpoint уникален в базе. Повторная подписка на общем устройстве после смены
    Члена семьи переводит подписку новому владельцу: старую строку удаляем явно,
    чтобы прежние уведомления не приходили на чужой экран.
    """
    with transaction.atomic():
        existing = PushSubscription.objects.select_for_update().filter(endpoint=endpoint).first()
        if existing is not None and existing.member_id != member_id:
            existing.delete()
        subscription, _ = PushSubscription.objects.update_or_create(
            endpoint=endpoint,
            defaults={"member_id": member_id, "p256dh": p256dh, "auth": auth},
        )
    return subscription


def unsubscribe(*, member_id: int, endpoint: str | None = None) -> int:
    """Удаляет подписки Члена семьи; с endpoint — только это устройство."""
    subscriptions = PushSubscription.objects.filter(member_id=member_id)
    if endpoint is not None:
        subscriptions = subscriptions.filter(endpoint=endpoint)
    deleted, _ = subscriptions.delete()
    return deleted


def notification_payload(
    title: str,
    body: str,
    *,
    url: str = "/assistant/chat/",
    tag: str = "summary",
) -> dict[str, Any]:
    """Собирает Declarative Web Push payload и сокращает текст до лимита.

    Формат `web_push: 8030` понимают iOS 18.4+ и новые браузеры; остальные
    обрабатывают тот же JSON обработчиком `push` service worker.
    """
    payload: dict[str, Any] = {
        "web_push": 8030,
        "notification": {"title": title, "body": body, "navigate": url, "tag": tag},
    }
    while _payload_size(payload) >= MAX_PAYLOAD_BYTES and body:
        body = body[:-1]
        payload["notification"]["body"] = body
    if _payload_size(payload) >= MAX_PAYLOAD_BYTES:
        raise PushPayloadTooLarge("Заголовок уведомления не влезает в лимит Web Push")
    return payload


def send_push(member_id: int, payload: Mapping[str, Any]) -> PushDelivery:
    """Отправляет payload на все подписки Члена семьи.

    Без подписок не делает сетевых запросов: согласие человека — это наличие
    подписки. Ошибка одного устройства не мешает остальным; подписка удаляется
    только при 404 или 410. Если VAPID не настроен, но подписки есть, бросает
    `PushNotConfigured`, чтобы вызывающий код не считал Сводку доставленной.
    """
    data = _encode(payload)
    subscriptions = list(PushSubscription.objects.filter(member_id=member_id))
    if not subscriptions:
        return PushDelivery(sent=0, expired=0, failed=0)
    push_settings = PushSettings.current()
    if not push_settings.is_configured:
        raise PushNotConfigured("VAPID-ключи не настроены")

    sent = expired = failed = 0
    for subscription in subscriptions:
        try:
            webpush(
                subscription_info={
                    "endpoint": subscription.endpoint,
                    "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
                },
                data=data,
                # pywebpush дописывает aud/exp в переданный dict, поэтому копия на каждый вызов.
                vapid_claims={"sub": push_settings.vapid_subject},
                vapid_private_key=push_settings.vapid_private_key,
                ttl=DEFAULT_TTL_SECONDS,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except WebPushException as exc:
            status = exc.status_code
            if status in (404, 410):
                PushSubscription.objects.filter(pk=subscription.pk, member_id=member_id).delete()
                expired += 1
            else:
                failed += 1
                logger.warning("Web Push не доставлен: статус %s", status)
        except OSError as exc:
            # Сетевая ошибка не говорит, что подписка протухла: она остаётся.
            failed += 1
            logger.warning("Web Push не доставлен: %s", type(exc).__name__)
        else:
            sent += 1
    return PushDelivery(sent=sent, expired=expired, failed=failed)


def _encode(payload: Mapping[str, Any]) -> str:
    """Сериализует payload и проверяет его размер до сетевого вызова."""
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(data.encode("utf-8")) >= MAX_PAYLOAD_BYTES:
        raise PushPayloadTooLarge("Payload больше лимита Web Push")
    return data


def _payload_size(payload: Mapping[str, Any]) -> int:
    """Размер сериализованного payload в байтах UTF-8."""
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


@task
def send_push_notification(member_id: int, payload: dict[str, Any]) -> None:
    """Фоновая точка входа для 08: ставится в очередь с JSON-совместимым payload."""
    send_push(member_id, payload)
