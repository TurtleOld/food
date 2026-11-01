import base64
import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.tasks import TaskResultStatus
from django.test import Client, TestCase
from django.urls import reverse
from django_tasks_db.models import DBTaskResult
from pywebpush import WebPushException

from core.models import PushSettings, PushSubscription
from core.push import (
    MAX_PAYLOAD_BYTES,
    PushNotConfigured,
    PushPayloadTooLarge,
    is_allowed_push_endpoint,
    is_configured,
    notification_payload,
    send_push,
    send_push_notification,
    subscribe,
    unsubscribe,
)

User = get_user_model()

FCM_ENDPOINT = "https://fcm.googleapis.com/send/abc"
APPLE_ENDPOINT = "https://web.push.apple.com/send/abc"
MOZILLA_ENDPOINT = "https://updates.push.services.mozilla.com/wpush/v2/abc"
WNS_ENDPOINT = "https://wns2-abc.notify.windows.com/w/?token=abc"


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


PUBLIC_KEY = _base64url(b"v" * 65)
P256DH = _base64url(b"p" * 65)
P256DH_OTHER = _base64url(b"q" * 65)
AUTH = _base64url(b"a" * 16)
PRIVATE_KEY = "private-key"
SUBJECT = "mailto:test@example.com"
KEYS = {"p256dh": P256DH, "auth": AUTH}


def configure_push(**overrides):
    """Записывает VAPID в единственную строку настроек; по умолчанию всё настроено."""
    values = {
        "vapid_public_key": PUBLIC_KEY,
        "vapid_private_key": PRIVATE_KEY,
        "vapid_subject": SUBJECT,
    }
    values.update(overrides)
    push = PushSettings.current()
    for field, value in values.items():
        setattr(push, field, value)
    push.save()
    return push


def clear_push():
    return configure_push(vapid_public_key="", vapid_private_key="", vapid_subject="")


def web_push_error(status):
    return WebPushException("push failed", response=mock.Mock(status_code=status))


class SubscriptionDomainTest(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice")
        self.bob = User.objects.create_user(username="bob")

    def test_repeat_subscription_updates_the_device_in_place(self):
        subscribe(member_id=self.alice.pk, endpoint=FCM_ENDPOINT, **KEYS)
        subscribe(
            member_id=self.alice.pk,
            endpoint=FCM_ENDPOINT,
            p256dh=P256DH_OTHER,
            auth=AUTH,
        )

        subscription = PushSubscription.objects.get()
        self.assertEqual(subscription.member, self.alice)
        self.assertEqual(subscription.p256dh, P256DH_OTHER)
        self.assertEqual(subscription.auth, AUTH)
        self.assertEqual(str(subscription), "Подписка alice")

    def test_device_re_registration_moves_ownership_explicitly(self):
        subscribe(member_id=self.alice.pk, endpoint=FCM_ENDPOINT, **KEYS)
        subscribe(member_id=self.bob.pk, endpoint=FCM_ENDPOINT, **KEYS)

        self.assertEqual(PushSubscription.objects.get().member, self.bob)
        self.assertEqual(PushSubscription.objects.count(), 1)

    def test_unsubscribe_is_scoped_to_the_member_and_device(self):
        subscribe(member_id=self.alice.pk, endpoint=FCM_ENDPOINT, **KEYS)
        subscribe(member_id=self.alice.pk, endpoint=APPLE_ENDPOINT, **KEYS)
        subscribe(member_id=self.bob.pk, endpoint=MOZILLA_ENDPOINT, **KEYS)

        removed = unsubscribe(member_id=self.alice.pk, endpoint=FCM_ENDPOINT)

        self.assertEqual(removed, 1)
        self.assertEqual(
            set(PushSubscription.objects.values_list("endpoint", flat=True)),
            {APPLE_ENDPOINT, MOZILLA_ENDPOINT},
        )

    def test_unsubscribe_without_endpoint_removes_all_member_subscriptions(self):
        subscribe(member_id=self.alice.pk, endpoint=FCM_ENDPOINT, **KEYS)
        subscribe(member_id=self.alice.pk, endpoint=APPLE_ENDPOINT, **KEYS)
        subscribe(member_id=self.bob.pk, endpoint=MOZILLA_ENDPOINT, **KEYS)

        removed = unsubscribe(member_id=self.alice.pk)

        self.assertEqual(removed, 2)
        self.assertEqual(
            set(PushSubscription.objects.values_list("endpoint", flat=True)),
            {MOZILLA_ENDPOINT},
        )


class PushEndpointTest(TestCase):
    def test_known_push_services_are_allowed(self):
        for endpoint in (FCM_ENDPOINT, APPLE_ENDPOINT, MOZILLA_ENDPOINT, WNS_ENDPOINT):
            with self.subTest(endpoint=endpoint):
                self.assertTrue(is_allowed_push_endpoint(endpoint))

    def test_unknown_or_unsafe_endpoints_are_rejected(self):
        for endpoint in (
            "http://fcm.googleapis.com/send/abc",
            "https://evil.example/send/abc",
            "https://fcm.googleapis.com.evil/send/abc",
            "https://user:pass@fcm.googleapis.com/send/abc",
            "https://127.0.0.1/send/abc",
            "not a url",
        ):
            with self.subTest(endpoint=endpoint):
                self.assertFalse(is_allowed_push_endpoint(endpoint))


class ConfigurationTest(TestCase):
    def test_settings_live_in_one_database_row(self):
        configure_push()
        configure_push(vapid_subject="mailto:other@example.com")

        self.assertEqual(PushSettings.objects.count(), 1)
        self.assertEqual(PushSettings.current().pk, PushSettings.SINGLETON_PK)
        self.assertEqual(PushSettings.current().vapid_subject, "mailto:other@example.com")

    def test_complete_vapid_settings_enable_push(self):
        configure_push()

        self.assertTrue(is_configured())
        self.assertEqual(str(PushSettings.current()), "Push настроен")

    def test_incomplete_vapid_settings_disable_push(self):
        configure_push(vapid_private_key="")

        self.assertFalse(is_configured())
        self.assertEqual(str(PushSettings.current()), "Push не настроен")

    def test_private_key_is_masked_on_the_staff_screen(self):
        configure_push(vapid_private_key="secret-1234")

        self.assertEqual(PushSettings.current().masked_private_key, "••••1234")

        configure_push(vapid_private_key="abc")
        self.assertEqual(PushSettings.current().masked_private_key, "•••")


class SendPushTest(TestCase):
    def setUp(self):
        clear_push()
        self.alice = User.objects.create_user(username="alice")
        self.bob = User.objects.create_user(username="bob")
        self.alice_subscription = subscribe(member_id=self.alice.pk, endpoint=FCM_ENDPOINT, **KEYS)

    def test_sends_only_to_the_members_own_devices(self):
        configure_push()
        subscribe(member_id=self.alice.pk, endpoint=APPLE_ENDPOINT, **KEYS)
        subscribe(member_id=self.bob.pk, endpoint=MOZILLA_ENDPOINT, **KEYS)
        payload = notification_payload("Сводка", "Итог дня")

        with mock.patch("core.push.webpush") as send:
            delivery = send_push(self.alice.pk, payload)

        self.assertEqual(delivery.sent, 2)
        self.assertEqual(delivery.expired, 0)
        self.assertEqual(delivery.failed, 0)
        endpoints = {call.kwargs["subscription_info"]["endpoint"] for call in send.call_args_list}
        self.assertEqual(endpoints, {FCM_ENDPOINT, APPLE_ENDPOINT})
        self.assertNotIn(MOZILLA_ENDPOINT, endpoints)
        first = send.call_args_list[0].kwargs
        self.assertEqual(json.loads(first["data"]), payload)
        self.assertEqual(first["ttl"], 12 * 60 * 60)
        self.assertEqual(first["vapid_private_key"], "private-key")
        self.assertEqual(first["vapid_claims"], {"sub": "mailto:test@example.com"})
        self.assertIsNot(
            send.call_args_list[0].kwargs["vapid_claims"],
            send.call_args_list[1].kwargs["vapid_claims"],
        )

    def test_without_subscriptions_nothing_is_sent(self):
        PushSubscription.objects.all().delete()

        with mock.patch("core.push.webpush") as send:
            delivery = send_push(self.alice.pk, {"notification": {"title": "Сводка"}})

        self.assertEqual((delivery.sent, delivery.expired, delivery.failed), (0, 0, 0))
        send.assert_not_called()

    def test_missing_vapid_with_a_subscription_is_an_error(self):
        with mock.patch("core.push.webpush") as send:
            with self.assertRaises(PushNotConfigured):
                send_push(self.alice.pk, {"notification": {"title": "Сводка"}})

        send.assert_not_called()

    def test_gone_and_not_found_delete_only_that_subscription(self):
        configure_push()
        for status in (404, 410):
            with self.subTest(status=status):
                subscription = subscribe(member_id=self.alice.pk, endpoint=FCM_ENDPOINT, **KEYS)
                with mock.patch("core.push.webpush", side_effect=web_push_error(status)):
                    delivery = send_push(self.alice.pk, {"notification": {"title": "Сводка"}})

                self.assertEqual((delivery.sent, delivery.expired, delivery.failed), (0, 1, 0))
                self.assertFalse(PushSubscription.objects.filter(pk=subscription.pk).exists())

    def test_other_errors_keep_the_subscription(self):
        configure_push()
        for status in (400, 403, 413, 429, 500):
            with self.subTest(status=status):
                with mock.patch("core.push.webpush", side_effect=web_push_error(status)):
                    delivery = send_push(self.alice.pk, {"notification": {"title": "Сводка"}})

                self.assertEqual((delivery.sent, delivery.expired, delivery.failed), (0, 0, 1))
                self.assertTrue(
                    PushSubscription.objects.filter(pk=self.alice_subscription.pk).exists()
                )

    def test_one_broken_device_does_not_stop_the_others(self):
        configure_push()
        subscribe(member_id=self.alice.pk, endpoint=APPLE_ENDPOINT, **KEYS)

        with mock.patch(
            "core.push.webpush",
            side_effect=[web_push_error(500), None],
        ):
            delivery = send_push(self.alice.pk, {"notification": {"title": "Сводка"}})

        self.assertEqual((delivery.sent, delivery.expired, delivery.failed), (1, 0, 1))
        self.assertEqual(PushSubscription.objects.count(), 2)

    def test_network_error_keeps_the_subscription(self):
        configure_push()

        with mock.patch("core.push.webpush", side_effect=OSError("network down")):
            delivery = send_push(self.alice.pk, {"notification": {"title": "Сводка"}})

        self.assertEqual((delivery.sent, delivery.expired, delivery.failed), (0, 0, 1))
        self.assertTrue(PushSubscription.objects.filter(pk=self.alice_subscription.pk).exists())

    def test_oversized_payload_is_rejected_before_sending(self):
        configure_push()
        with mock.patch("core.push.webpush") as send:
            with self.assertRaises(PushPayloadTooLarge):
                send_push(self.alice.pk, {"body": "я" * 2000})

        send.assert_not_called()


class BackgroundEntrypointTest(TestCase):
    def test_task_entrypoint_calls_send_push_with_json_arguments(self):
        payload = {"web_push": 8030, "notification": {"title": "Сводка"}}

        with mock.patch("core.push.send_push") as send:
            send_push_notification.call(7, payload)

        send.assert_called_once_with(7, payload)

    def test_task_is_enqueued_into_the_database_queue(self):
        payload = {"web_push": 8030, "notification": {"title": "Сводка"}}

        result = send_push_notification.enqueue(7, payload)

        self.assertEqual(result.status, TaskResultStatus.READY)
        stored = DBTaskResult.objects.get(task_path="core.push.send_push_notification")
        self.assertEqual(stored.args_kwargs, {"args": [7, payload], "kwargs": {}})


class NotificationPayloadTest(TestCase):
    def test_short_payload_is_kept_as_is(self):
        payload = notification_payload("Сводка", "Итог дня", url="/day/2026-11-01/")

        self.assertEqual(payload["web_push"], 8030)
        self.assertEqual(payload["notification"]["title"], "Сводка")
        self.assertEqual(payload["notification"]["body"], "Итог дня")
        self.assertEqual(payload["notification"]["navigate"], "/day/2026-11-01/")
        self.assertEqual(payload["notification"]["tag"], "summary")

    def test_default_navigation_opens_the_assistant_chat(self):
        payload = notification_payload("Сводка", "Итог дня")

        self.assertEqual(payload["notification"]["navigate"], "/assistant/chat/")

    def test_long_body_is_truncated_below_the_limit(self):
        payload = notification_payload("Сводка", "я" * (MAX_PAYLOAD_BYTES * 2))

        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        self.assertLess(len(encoded), MAX_PAYLOAD_BYTES)
        self.assertTrue(payload["notification"]["body"])
        self.assertEqual(payload["notification"]["title"], "Сводка")

    def test_too_long_title_cannot_be_truncated_safely(self):
        with self.assertRaises(PushPayloadTooLarge):
            notification_payload("я" * MAX_PAYLOAD_BYTES, "")


class NotificationPageTest(TestCase):
    def setUp(self):
        configure_push()
        self.member = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.force_login(self.member)
        self.url = reverse("core:notifications")

    def test_page_requires_login(self):
        self.client.logout()

        response = self.client.get(self.url)

        self.assertRedirects(response, f"/login/?next={self.url}")

    def test_profile_links_to_notifications(self):
        response = self.client.get(reverse("core:profile"))

        self.assertContains(response, f'href="{self.url}"')
        self.assertContains(response, "Настроить уведомления")

    def test_page_exposes_the_public_key_and_opt_in(self):
        response = self.client.get(self.url)

        self.assertContains(response, f'data-vapid-public-key="{PUBLIC_KEY}"')
        self.assertContains(response, "Включить уведомления")
        self.assertContains(response, reverse("core:push_subscribe"))

    def test_page_shows_only_the_members_own_subscription_count(self):
        bob = User.objects.create_user(username="bob")
        subscribe(member_id=bob.pk, endpoint=FCM_ENDPOINT, **KEYS)

        response = self.client.get(self.url)

        self.assertContains(response, "Уведомления выключены.")
        self.assertNotContains(response, FCM_ENDPOINT)

    def test_page_explains_when_push_is_not_configured(self):
        clear_push()

        response = self.client.get(self.url)

        self.assertContains(response, "не настроены на сервере")
        self.assertNotContains(response, "Включить уведомления")

    def test_regular_member_does_not_see_staff_settings_link(self):
        response = self.client.get(self.url)

        self.assertNotContains(response, "Настройки Push для администратора")


class PushSettingsViewTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username="admin", password="s3cret-pass", is_staff=True
        )
        self.member = User.objects.create_user(username="alice", password="s3cret-pass")
        self.url = reverse("core:push_settings")
        self.notifications = reverse("core:notifications")
        self.data = {
            "vapid_public_key": PUBLIC_KEY,
            "vapid_private_key": "private-key",
            "vapid_subject": "mailto:staff@example.com",
        }

    def test_page_requires_login(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)

    def test_regular_member_gets_forbidden(self):
        self.client.force_login(self.member)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)

    def test_staff_member_saves_vapid_settings(self):
        self.client.force_login(self.staff)

        response = self.client.post(self.url, self.data, follow=True)

        self.assertRedirects(response, self.notifications)
        push = PushSettings.current()
        self.assertTrue(push.is_configured)
        self.assertEqual(push.vapid_public_key, PUBLIC_KEY)
        self.assertEqual(push.vapid_private_key, "private-key")
        self.assertEqual(push.vapid_subject, "mailto:staff@example.com")
        self.assertContains(response, "Включить уведомления")

    def test_blank_private_key_keeps_the_saved_one(self):
        configure_push(vapid_private_key="secret-key")
        self.client.force_login(self.staff)

        self.client.post(self.url, {**self.data, "vapid_private_key": ""})

        self.assertEqual(PushSettings.current().vapid_private_key, "secret-key")

    def test_saved_private_key_is_not_rendered(self):
        configure_push(vapid_private_key="secret-key")
        self.client.force_login(self.staff)

        response = self.client.get(self.url)

        self.assertContains(response, "••••-key")
        self.assertNotContains(response, "secret-key")

    def test_invalid_public_key_is_rejected(self):
        self.client.force_login(self.staff)

        response = self.client.post(
            self.url, {**self.data, "vapid_public_key": _base64url(b"short")}
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Неверный размер публичного ключа")
        self.assertEqual(PushSettings.current().vapid_public_key, "")

    def test_staff_sees_settings_link_on_notifications_page(self):
        self.client.force_login(self.staff)

        response = self.client.get(self.notifications)

        self.assertContains(response, self.url)
        self.assertContains(response, "Настройки Push для администратора")


class SubscribeViewTest(TestCase):
    def setUp(self):
        configure_push()
        self.member = User.objects.create_user(username="alice", password="s3cret-pass")
        self.bob = User.objects.create_user(username="bob")
        self.client.force_login(self.member)
        self.url = reverse("core:push_subscribe")
        self.notifications = reverse("core:notifications")
        self.data = {"endpoint": FCM_ENDPOINT, **KEYS}

    def test_valid_subscription_is_saved_for_the_current_member(self):
        response = self.client.post(self.url, self.data)

        self.assertRedirects(response, self.notifications)
        subscription = PushSubscription.objects.get()
        self.assertEqual(subscription.member, self.member)
        self.assertEqual(subscription.endpoint, FCM_ENDPOINT)

    def test_repeat_post_updates_instead_of_duplicating(self):
        self.client.post(self.url, self.data)
        self.client.post(self.url, {**self.data, "p256dh": P256DH_OTHER})

        self.assertEqual(PushSubscription.objects.count(), 1)
        self.assertEqual(PushSubscription.objects.get().p256dh, P256DH_OTHER)

    def test_invalid_endpoint_is_rejected_without_a_record(self):
        for endpoint in (
            "http://fcm.googleapis.com/send/abc",
            "https://evil.example/send/abc",
            "https://127.0.0.1/send/abc",
        ):
            with self.subTest(endpoint=endpoint):
                response = self.client.post(
                    self.url, {**self.data, "endpoint": endpoint}, follow=True
                )

                self.assertContains(response, "Не удалось включить уведомления")
                self.assertFalse(PushSubscription.objects.exists())

    def test_invalid_key_sizes_are_rejected_without_a_record(self):
        for field in ("p256dh", "auth"):
            with self.subTest(field=field):
                response = self.client.post(self.url, {**self.data, field: "short"}, follow=True)

                self.assertContains(response, "Не удалось включить уведомления")
                self.assertFalse(PushSubscription.objects.exists())

    def test_non_base64_key_is_rejected_without_a_record(self):
        response = self.client.post(self.url, {**self.data, "p256dh": "!!!!"}, follow=True)

        self.assertContains(response, "Не удалось включить уведомления")
        self.assertFalse(PushSubscription.objects.exists())

    def test_missing_keys_are_rejected_without_a_record(self):
        response = self.client.post(self.url, {"endpoint": FCM_ENDPOINT}, follow=True)

        self.assertContains(response, "Не удалось включить уведомления")
        self.assertFalse(PushSubscription.objects.exists())

    def test_get_is_not_allowed(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 405)

    def test_post_without_csrf_is_rejected(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.member)

        response = csrf_client.post(self.url, self.data)

        self.assertEqual(response.status_code, 403)
        self.assertFalse(PushSubscription.objects.exists())


class UnsubscribeViewTest(TestCase):
    def setUp(self):
        configure_push()
        self.member = User.objects.create_user(username="alice", password="s3cret-pass")
        self.bob = User.objects.create_user(username="bob")
        self.client.force_login(self.member)
        self.url = reverse("core:push_unsubscribe")
        self.notifications = reverse("core:notifications")

    def test_device_unsubscribe_removes_only_the_members_endpoint(self):
        subscribe(member_id=self.member.pk, endpoint=FCM_ENDPOINT, **KEYS)
        subscribe(member_id=self.member.pk, endpoint=APPLE_ENDPOINT, **KEYS)
        subscribe(member_id=self.bob.pk, endpoint=MOZILLA_ENDPOINT, **KEYS)

        response = self.client.post(self.url, {"endpoint": FCM_ENDPOINT})

        self.assertRedirects(response, self.notifications)
        self.assertEqual(
            set(PushSubscription.objects.values_list("endpoint", flat=True)),
            {APPLE_ENDPOINT, MOZILLA_ENDPOINT},
        )

    def test_unsubscribe_without_endpoint_removes_all_own_subscriptions(self):
        subscribe(member_id=self.member.pk, endpoint=FCM_ENDPOINT, **KEYS)
        subscribe(member_id=self.member.pk, endpoint=APPLE_ENDPOINT, **KEYS)
        subscribe(member_id=self.bob.pk, endpoint=MOZILLA_ENDPOINT, **KEYS)

        self.client.post(self.url, {"endpoint": ""})

        self.assertEqual(
            set(PushSubscription.objects.values_list("endpoint", flat=True)),
            {MOZILLA_ENDPOINT},
        )

    def test_cannot_unsubscribe_another_members_device(self):
        subscribe(member_id=self.member.pk, endpoint=FCM_ENDPOINT, **KEYS)
        subscribe(member_id=self.bob.pk, endpoint=MOZILLA_ENDPOINT, **KEYS)

        self.client.post(self.url, {"endpoint": MOZILLA_ENDPOINT})

        self.assertTrue(
            PushSubscription.objects.filter(endpoint=MOZILLA_ENDPOINT, member=self.bob).exists()
        )
        self.assertTrue(
            PushSubscription.objects.filter(endpoint=FCM_ENDPOINT, member=self.member).exists()
        )

    def test_unsubscribe_reports_when_nothing_was_removed(self):
        response = self.client.post(self.url, {"endpoint": ""}, follow=True)

        self.assertContains(response, "Уведомления уже выключены")

    def test_invalid_endpoint_is_rejected_and_keeps_subscriptions(self):
        subscribe(member_id=self.member.pk, endpoint=FCM_ENDPOINT, **KEYS)

        response = self.client.post(self.url, {"endpoint": "not a url"}, follow=True)

        self.assertContains(response, "Не удалось выключить уведомления")
        self.assertTrue(PushSubscription.objects.filter(member=self.member).exists())

    def test_get_is_not_allowed(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 405)
