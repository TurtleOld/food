import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from assistant.budget import BudgetUnavailable, month_summary, release, reserve, settle
from assistant.models import BudgetReservation, LLMSettings, UsageRecord

User = get_user_model()

FORM_DATA = {
    "base_url": "https://llm.test/v1",
    "model": "deepseek-flash",
    "api_key": "",
    "monthly_budget_rub": "500.00",
    "input_price_rub": "100.00",
    "cached_price_rub": "20.00",
    "output_price_rub": "300.00",
    "max_input_tokens": "12000",
    "max_output_tokens": "1000",
    "timeout_seconds": "10",
}


def configure(**overrides):
    values = {
        "base_url": "https://llm.test/v1",
        "model": "deepseek-flash",
        "api_key": "sk-live-secret-1234",
        "monthly_budget_rub": Decimal("500.00"),
        "input_price_rub": Decimal("100.00"),
        "cached_price_rub": Decimal("20.00"),
        "output_price_rub": Decimal("300.00"),
        "max_input_tokens": 12000,
        "max_output_tokens": 1000,
        "timeout_seconds": 10,
    }
    values.update(overrides)
    return LLMSettings.objects.create(**values)


def add_usage(cost="100.00", created_at=None):
    settings = LLMSettings.current()
    usage = UsageRecord.objects.create(
        reservation=BudgetReservation.objects.create(
            request_id=f"used-{UsageRecord.objects.count()}",
            amount_rub=Decimal(cost),
            model=settings.model,
            input_price_rub=Decimal("100.00"),
            cached_price_rub=Decimal("20.00"),
            output_price_rub=Decimal("300.00"),
            status=BudgetReservation.Status.SETTLED,
        ),
        model=settings.model,
        input_tokens=1_000_000,
        cached_input_tokens=0,
        output_tokens=0,
        input_price_rub=Decimal("100.00"),
        cached_price_rub=Decimal("20.00"),
        output_price_rub=Decimal("300.00"),
        cost_rub=Decimal(cost),
    )
    if created_at is not None:
        UsageRecord.objects.filter(pk=usage.pk).update(created_at=created_at)
    return usage


class BudgetPageTests(TestCase):
    def setUp(self):
        self.member = User.objects.create_user(username="alice", password="s3cret-pass")
        self.admin = User.objects.create_user(
            username="admin", password="s3cret-pass", is_staff=True
        )

    def test_page_requires_login(self):
        response = self.client.get(reverse("assistant:budget"))

        self.assertRedirects(
            response,
            f"{reverse('core:login')}?next={reverse('assistant:budget')}",
        )

    def test_unconfigured_assistant_says_requests_are_disabled(self):
        self.client.force_login(self.member)

        response = self.client.get(reverse("assistant:budget"))

        self.assertContains(response, "не настроен")
        self.assertContains(response, "отключены")
        self.assertNotContains(response, reverse("assistant:settings"))

    def test_member_sees_spend_and_remaining_but_not_the_key_or_settings_link(self):
        configure()
        add_usage("100.00")
        self.client.force_login(self.member)

        response = self.client.get(reverse("assistant:budget"))

        self.assertContains(response, "100,00")
        self.assertContains(response, "400,00")
        self.assertNotContains(response, "sk-live-secret-1234")
        self.assertNotContains(response, reverse("assistant:settings"))

    def test_budget_is_shared_by_the_whole_family(self):
        configure()
        add_usage("100.00")
        bob = User.objects.create_user(username="bob", password="s3cret-pass")

        self.client.force_login(self.member)
        alice_page = self.client.get(reverse("assistant:budget"))
        self.client.force_login(bob)
        bob_page = self.client.get(reverse("assistant:budget"))

        self.assertContains(alice_page, "400,00")
        self.assertContains(bob_page, "400,00")

    def test_admin_sees_a_link_to_settings(self):
        configure()
        self.client.force_login(self.admin)

        response = self.client.get(reverse("assistant:budget"))

        self.assertContains(response, reverse("assistant:settings"))


class SettingsPageTests(TestCase):
    def setUp(self):
        self.member = User.objects.create_user(username="alice", password="s3cret-pass")
        self.admin = User.objects.create_user(
            username="admin", password="s3cret-pass", is_staff=True
        )

    def test_ordinary_member_cannot_open_or_change_settings(self):
        settings = configure()
        self.client.force_login(self.member)

        page = self.client.get(reverse("assistant:settings"))
        changed = self.client.post(
            reverse("assistant:settings"), {**FORM_DATA, "monthly_budget_rub": "9999"}
        )

        self.assertEqual(page.status_code, 403)
        self.assertEqual(changed.status_code, 403)
        settings.refresh_from_db()
        self.assertEqual(settings.monthly_budget_rub, Decimal("500.00"))

    def test_admin_sees_the_key_masked_and_never_in_full(self):
        configure()
        self.client.force_login(self.admin)

        response = self.client.get(reverse("assistant:settings"))

        self.assertContains(response, "••••1234")
        self.assertNotContains(response, "sk-live-secret-1234")

    def test_saving_keeps_the_key_when_the_field_is_blank(self):
        configure()
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("assistant:settings"),
            {**FORM_DATA, "monthly_budget_rub": "750.00"},
            follow=True,
        )

        self.assertContains(response, "Настройки сохранены")
        settings = LLMSettings.current()
        self.assertEqual(settings.api_key, "sk-live-secret-1234")
        self.assertEqual(settings.monthly_budget_rub, Decimal("750.00"))

    def test_saving_a_new_key_replaces_the_old_one(self):
        configure()
        self.client.force_login(self.admin)

        self.client.post(reverse("assistant:settings"), {**FORM_DATA, "api_key": "sk-new-9999"})

        self.assertEqual(LLMSettings.current().api_key, "sk-new-9999")

    def test_first_setup_requires_a_key_and_valid_numbers(self):
        self.client.force_login(self.admin)

        missing_key = self.client.post(reverse("assistant:settings"), FORM_DATA)
        negative = self.client.post(
            reverse("assistant:settings"),
            {**FORM_DATA, "api_key": "sk-new-9999", "input_price_rub": "-1"},
        )

        self.assertEqual(missing_key.status_code, 200)
        self.assertContains(missing_key, "ключ")
        self.assertEqual(negative.status_code, 200)
        self.assertFalse(LLMSettings.objects.exists())


class ReservationTests(TestCase):
    def setUp(self):
        self.config = configure(
            monthly_budget_rub=Decimal("100.00"),
            input_price_rub=Decimal("60.00"),
            cached_price_rub=Decimal("0"),
            output_price_rub=Decimal("0"),
            max_input_tokens=1_000_000,
            max_output_tokens=1,
        )

    def test_reservation_holds_money_and_blocks_a_second_request(self):
        first = reserve(request_id="req-1")

        self.assertEqual(first.amount_rub, Decimal("60.000000"))
        self.assertEqual(month_summary().remaining, Decimal("40.000000"))
        with self.assertRaisesMessage(BudgetUnavailable, "исчерпан"):
            reserve(request_id="req-2")

    def test_release_returns_the_held_money(self):
        first = reserve(request_id="req-1")

        release(first)

        self.assertEqual(first.status, BudgetReservation.Status.RELEASED)
        self.assertEqual(month_summary().remaining, Decimal("100.000000"))
        self.assertEqual(reserve(request_id="req-2").amount_rub, Decimal("60.000000"))

    def test_settlement_records_actual_cost_with_the_tariff_captured_at_request(self):
        self.config.input_price_rub = Decimal("100.00")
        self.config.cached_price_rub = Decimal("20.00")
        self.config.output_price_rub = Decimal("300.00")
        self.config.monthly_budget_rub = Decimal("200.00")
        self.config.save()
        reservation = reserve(request_id="req-1")
        self.config.input_price_rub = Decimal("999.00")
        self.config.save()

        usage = settle(
            reservation,
            input_tokens=100_000,
            cached_input_tokens=50_000,
            output_tokens=100_000,
        )

        self.assertEqual(usage.cost_rub, Decimal("36.000000"))
        self.assertEqual(usage.input_price_rub, Decimal("100.0000"))
        self.assertEqual(usage.cached_price_rub, Decimal("20.0000"))
        self.assertEqual(usage.output_price_rub, Decimal("300.0000"))
        self.assertEqual(reservation.status, BudgetReservation.Status.SETTLED)
        summary = month_summary()
        self.assertEqual(summary.spent, Decimal("36.000000"))
        self.assertEqual(summary.reserved, Decimal("0"))
        self.assertEqual(summary.remaining, Decimal("164.000000"))

    def test_previous_month_usage_is_not_part_of_this_month(self):
        month_start = timezone.localtime().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        add_usage("60.00", created_at=month_start - datetime.timedelta(days=1))

        self.assertEqual(month_summary().spent, Decimal("0"))
        self.assertEqual(month_summary().remaining, Decimal("100.000000"))

    def test_stale_pending_reservation_is_released_before_a_new_request(self):
        stale = BudgetReservation.objects.create(
            request_id="crashed",
            amount_rub=Decimal("90.00"),
            model="deepseek-flash",
            input_price_rub=Decimal("60.00"),
            cached_price_rub=Decimal("0"),
            output_price_rub=Decimal("0"),
        )
        BudgetReservation.objects.filter(pk=stale.pk).update(
            created_at=timezone.now() - datetime.timedelta(minutes=10)
        )

        reserve(request_id="req-1")

        stale.refresh_from_db()
        self.assertEqual(stale.status, BudgetReservation.Status.RELEASED)

    def test_reservation_requires_configured_provider_and_tariffs(self):
        self.config.input_price_rub = None
        self.config.save()

        with self.assertRaisesMessage(BudgetUnavailable, "не настроен"):
            reserve(request_id="req-1")
