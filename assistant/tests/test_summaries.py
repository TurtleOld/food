"""Сводки: цифры, совет модели, расписание, доставка в ленту и Push."""

import datetime
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django_tasks_db.models import DBTaskResult

from assistant.gateway import LLMGatewayError, LLMResult, Usage
from assistant.models import Message, Summary, SummarySettings
from assistant.summaries import (
    ADVICE_PROMPT,
    build_text,
    due_summaries,
    evening_text,
    generate_summary,
    schedule_due,
    weekly_text,
)
from assistant.tasks import push_summary_task
from core.jobs import run_tick
from core.models import DailyTarget, DiaryEntry, Product, WeightMeasurement
from core.push import PushNotConfigured

User = get_user_model()

MONDAY = datetime.date(2026, 10, 26)
SUNDAY = datetime.date(2026, 11, 1)


def local(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime.datetime:
    """Момент местного времени приложения в aware-виде."""
    return timezone.make_aware(datetime.datetime(year, month, day, hour, minute))


def make_product(member) -> Product:
    return Product.objects.create(
        name="Творог",
        base_unit=Product.BaseUnit.GRAM,
        calories=Decimal("100.0"),
        proteins=Decimal("10.0"),
        fats=Decimal("5.0"),
        carbs=Decimal("3.0"),
        author=member,
    )


def add_entry(member, product, day: datetime.date, amount: str = "200") -> DiaryEntry:
    return DiaryEntry.objects.create(
        member=member,
        date=day,
        meal_type=DiaryEntry.MealType.BREAKFAST,
        product=product,
        amount=Decimal(amount),
    )


class SummaryTextTest(TestCase):
    """Детерминированные цифры вечерней и недельной Сводки."""

    def setUp(self) -> None:
        self.member = User.objects.create_user(username="alice", password="s3cret-pass")
        self.product = make_product(self.member)
        self.today = datetime.date(2026, 11, 1)
        self.yesterday = self.today - datetime.timedelta(days=1)

    def test_evening_compares_day_with_target_and_yesterday(self) -> None:
        add_entry(self.member, self.product, self.today, "200")
        add_entry(self.member, self.product, self.yesterday, "100")
        DailyTarget.objects.create(
            member=self.member,
            calories=Decimal("2000.0"),
            proteins=Decimal("100.0"),
            fats=Decimal("70.0"),
            carbs=Decimal("250.0"),
        )

        text = evening_text(self.member.pk, self.today)

        self.assertIn("Калории: на 1800 ккал меньше цели 2000 ккал.", text)
        self.assertIn("Белки: 20 из 100 г.", text)
        self.assertIn("Жиры: 10 из 70 г.", text)
        self.assertIn("Углеводы: 6 из 250 г.", text)
        self.assertIn("Вчера: 100 ккал.", text)

    def test_evening_without_target_still_reports_numbers(self) -> None:
        add_entry(self.member, self.product, self.today)

        text = evening_text(self.member.pk, self.today)

        self.assertIn("Калории: 200 ккал. Суточная цель не задана.", text)
        self.assertIn("Белки: 20 г.", text)

    def test_empty_day_is_a_reminder_without_numbers(self) -> None:
        text = evening_text(self.member.pk, self.today)

        self.assertIn("Сегодня в Дневнике нет записей", text)
        self.assertNotIn("Калории:", text)

    def test_yesterday_reminder_when_yesterday_was_empty(self) -> None:
        add_entry(self.member, self.product, self.today)

        self.assertIn("Вчера записей не было.", evening_text(self.member.pk, self.today))

    def test_weekly_totals_seven_local_days_and_weight_trajectory(self) -> None:
        for offset in range(7):
            add_entry(self.member, self.product, MONDAY + datetime.timedelta(days=offset), "100")
        WeightMeasurement.objects.create(member=self.member, date=MONDAY, weight_kg=Decimal("80.0"))
        WeightMeasurement.objects.create(member=self.member, date=SUNDAY, weight_kg=Decimal("79.6"))
        DailyTarget.objects.create(
            member=self.member,
            calories=Decimal("2000.0"),
            proteins=Decimal("100.0"),
            fats=Decimal("70.0"),
            carbs=Decimal("250.0"),
        )

        text = weekly_text(self.member.pk, MONDAY)

        self.assertIn("За 7 дней: 700 ккал, в среднем 100 ккал в день.", text)
        self.assertIn("Белки: 70 г, жиры: 35 г, углеводы: 21 г.", text)
        self.assertIn("Цель: 2000 ккал в день.", text)
        self.assertIn("Вес: 79,6 кг (1 ноября).", text)
        self.assertIn("−0,4 кг к прошлому Замеру.", text)

    def test_empty_week_reports_no_entries_and_keeps_weight(self) -> None:
        WeightMeasurement.objects.create(member=self.member, date=SUNDAY, weight_kg=Decimal("80.0"))

        text = weekly_text(self.member.pk, MONDAY)

        self.assertIn("За неделю в Дневнике нет записей", text)
        self.assertIn("Вес: 80 кг", text)
        self.assertNotIn("Совет:", text)

    def test_advice_is_added_through_the_accounted_gateway(self) -> None:
        add_entry(self.member, self.product, SUNDAY)
        result = LLMResult(
            text="Добавьте овощи к ужину.",
            tool_calls=(),
            usage=Usage(input_tokens=100, cached_input_tokens=0, output_tokens=20),
            cost_rub=Decimal("0.001"),
            model="deepseek-flash",
        )

        with mock.patch("assistant.summaries.complete", return_value=result) as complete:
            text = build_text(self.member.pk, Summary.Kind.EVENING, SUNDAY)

        self.assertIn("Совет: Добавьте овощи к ужину.", text)
        messages = complete.call_args.args[0]
        self.assertEqual(messages[0]["content"], ADVICE_PROMPT)
        self.assertIn("не назначай лечение", ADVICE_PROMPT)
        self.assertIn("Калории:", messages[1]["content"])

    def test_empty_day_does_not_spend_budget_on_advice(self) -> None:
        with mock.patch("assistant.summaries.complete") as complete:
            text = build_text(self.member.pk, Summary.Kind.EVENING, SUNDAY)

        complete.assert_not_called()
        self.assertIn("нет записей", text)

    def test_advice_is_optional_when_budget_is_exhausted(self) -> None:
        from assistant.budget import BudgetUnavailable

        with mock.patch(
            "assistant.summaries.complete", side_effect=BudgetUnavailable("нет бюджета")
        ):
            text = build_text(self.member.pk, Summary.Kind.EVENING, SUNDAY)

        self.assertIn("Сегодня в Дневнике нет записей", text)
        self.assertNotIn("Совет:", text)

    def test_advice_is_optional_when_provider_fails(self) -> None:
        with mock.patch("assistant.summaries.complete", side_effect=LLMGatewayError("timeout")):
            text = build_text(self.member.pk, Summary.Kind.EVENING, SUNDAY)

        self.assertIn("Сегодня в Дневнике нет записей", text)
        self.assertNotIn("Совет:", text)


class SummaryDeliveryTest(TestCase):
    """Доставка: один текст, одно сообщение в ленте, отдельная задача Push."""

    def setUp(self) -> None:
        self.member = User.objects.create_user(username="alice", password="s3cret-pass")
        self.product = make_product(self.member)
        self.day = datetime.date(2026, 11, 1)

    def generate(self, kind: str = Summary.Kind.EVENING, period: datetime.date | None = None):
        with self.captureOnCommitCallbacks(execute=True):
            return generate_summary(self.member.pk, kind, period or self.day)

    def test_repeat_generation_keeps_one_summary_and_one_message(self) -> None:
        first = self.generate()
        assert first is not None
        second = self.generate()

        self.assertIsNone(second)
        self.assertEqual(Summary.objects.count(), 1)
        self.assertEqual(Message.objects.count(), 1)
        push = DBTaskResult.objects.get(task_path="assistant.tasks.push_summary_task")
        self.assertEqual(push.args_kwargs["args"][0], self.member.pk)
        self.assertEqual(push.args_kwargs["args"][1]["web_push"], 8030)

    def test_database_rejects_a_second_summary_for_the_same_period(self) -> None:
        self.generate()

        with self.assertRaises(IntegrityError):
            Summary.objects.create(
                member=self.member,
                kind=Summary.Kind.EVENING,
                period_start=self.day,
                text="второй текст",
            )

    def test_summary_text_does_not_change_after_new_entries(self) -> None:
        summary = self.generate()
        assert summary is not None
        stored = summary.text

        add_entry(self.member, self.product, self.day, "500")
        self.generate()

        summary.refresh_from_db()
        self.assertEqual(summary.text, stored)
        self.assertNotIn("Калории:", summary.text)

    def test_summary_appears_in_the_assistant_feed(self) -> None:
        add_entry(self.member, self.product, self.day, "200")
        summary = self.generate()
        assert summary is not None
        self.client.force_login(self.member)

        response = self.client.get(reverse("assistant:chat"))

        self.assertContains(response, "Вечерняя сводка")
        self.assertContains(response, "200 ккал")

    def test_summary_is_in_the_feed_even_when_push_is_unavailable(self) -> None:
        add_entry(self.member, self.product, self.day, "200")
        summary = self.generate()
        assert summary is not None
        payload = {"web_push": 8030, "notification": {"title": "Сводка", "body": "текст"}}

        with mock.patch("assistant.tasks.send_push", side_effect=PushNotConfigured("нет VAPID")):
            push_summary_task.call(self.member.pk, payload)

        self.assertEqual(Summary.objects.count(), 1)
        self.assertEqual(Message.objects.count(), 1)
        self.assertTrue(Message.objects.filter(text=summary.text).exists())

    def test_inactive_member_gets_no_summary(self) -> None:
        self.member.is_active = False
        self.member.save(update_fields=["is_active"])

        self.assertIsNone(self.generate())
        self.assertEqual(Summary.objects.count(), 0)


class SummaryScheduleTest(TestCase):
    """Расписание по местному календарю: границы времени, воскресенье, отключение."""

    def setUp(self) -> None:
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.bob = User.objects.create_user(username="bob", password="s3cret-pass")

    def test_evening_is_not_due_before_local_time(self) -> None:
        self.assertEqual(due_summaries(local(2026, 11, 1, 20, 59)), [])
        due = due_summaries(local(2026, 11, 1, 21, 0))
        evening = [item for item in due if item[1] == Summary.Kind.EVENING]
        self.assertEqual(
            evening,
            [
                (self.alice.pk, Summary.Kind.EVENING, SUNDAY),
                (self.bob.pk, Summary.Kind.EVENING, SUNDAY),
            ],
        )

    def test_evening_respects_individual_time(self) -> None:
        SummarySettings.objects.filter(member=self.alice).update(evening_time=datetime.time(19, 30))
        SummarySettings.for_member(self.bob)
        SummarySettings.objects.filter(member=self.bob).update(evening_time=datetime.time(22, 0))

        due = due_summaries(local(2026, 11, 1, 21, 0))

        evening = [item for item in due if item[1] == Summary.Kind.EVENING]
        self.assertEqual(evening, [(self.alice.pk, Summary.Kind.EVENING, SUNDAY)])

    def test_disabled_evening_is_not_scheduled(self) -> None:
        SummarySettings.for_member(self.alice)
        SummarySettings.objects.filter(member=self.alice).update(evening_enabled=False)

        due = due_summaries(local(2026, 11, 1, 23, 0))

        evening = [item for item in due if item[1] == Summary.Kind.EVENING]
        self.assertNotIn(self.alice.pk, [member_id for member_id, _, _ in evening])

    def test_weekly_is_due_only_on_sunday_but_covers_the_local_week(self) -> None:
        due = due_summaries(local(2026, 11, 1, 21, 0))

        self.assertIn((self.alice.pk, Summary.Kind.WEEKLY, MONDAY), due)

    def test_weekly_is_not_due_just_after_local_midnight_on_monday(self) -> None:
        # 21:30 UTC воскресенья — это уже 00:30 понедельника по местному времени.
        moment = datetime.datetime(2026, 11, 1, 21, 30, tzinfo=datetime.UTC)

        due = due_summaries(moment)

        self.assertEqual([item for item in due if item[1] == Summary.Kind.WEEKLY], [])

    def test_weekly_can_be_disabled_independently(self) -> None:
        SummarySettings.for_member(self.alice)
        SummarySettings.objects.filter(member=self.alice).update(weekly_enabled=False)

        due = due_summaries(local(2026, 11, 1, 21, 0))

        self.assertNotIn((self.alice.pk, Summary.Kind.WEEKLY, MONDAY), due)
        self.assertIn((self.alice.pk, Summary.Kind.EVENING, SUNDAY), due)

    def test_repeat_tick_does_not_enqueue_the_same_summary_twice(self) -> None:
        moment = local(2026, 11, 1, 21, 0)

        first = schedule_due(moment)
        generate_for_all(moment)
        second = schedule_due(moment)

        self.assertEqual(first, 4)
        self.assertEqual(second, 0)
        self.assertEqual(Summary.objects.count(), 4)

    def test_background_tick_enqueues_due_summary_tasks(self) -> None:
        for member in (self.alice, self.bob):
            SummarySettings.for_member(member)
        SummarySettings.objects.update(
            evening_time=datetime.time(0, 0),
            weekly_enabled=False,
        )

        run_tick()

        stored = DBTaskResult.objects.filter(task_path="assistant.tasks.generate_summary_task")
        self.assertEqual(stored.count(), 2)
        self.assertEqual(
            sorted(result.args_kwargs["args"][0] for result in stored),
            sorted([self.alice.pk, self.bob.pk]),
        )

    def test_sent_summary_is_not_scheduled_again(self) -> None:
        moment = local(2026, 11, 1, 21, 0)
        generate_for_all(moment)

        self.assertEqual(schedule_due(moment), 0)


def generate_for_all(moment: datetime.datetime) -> None:
    """Выполняет поставленные Сводки так, как это сделал бы воркер."""
    for member_id, kind, period_start in due_summaries(moment):
        generate_summary(member_id, kind, period_start, now=moment)


class SummarySettingsPageTest(TestCase):
    """Экран расписания: свои настройки, чужие не меняются."""

    def setUp(self) -> None:
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.bob = User.objects.create_user(username="bob", password="s3cret-pass")

    def test_anonymous_visitor_is_redirected_to_login(self) -> None:
        response = self.client.get(reverse("assistant:summaries"))

        self.assertRedirects(
            response,
            f"{reverse('core:login')}?next={reverse('assistant:summaries')}",
        )

    def test_defaults_are_evening_and_weekly_at_21(self) -> None:
        self.client.force_login(self.alice)

        response = self.client.get(reverse("assistant:summaries"))

        self.assertContains(response, "21:00")
        settings = SummarySettings.objects.get(member=self.alice)
        self.assertEqual(settings.evening_time, datetime.time(21, 0))
        self.assertEqual(settings.weekly_time, datetime.time(21, 0))

    def test_member_saves_own_time_and_disables_evening(self) -> None:
        self.client.force_login(self.alice)

        response = self.client.post(
            reverse("assistant:summaries"),
            {
                "evening_time": "19:45",
                "weekly_time": "10:15",
                "weekly_enabled": "on",
            },
        )

        self.assertRedirects(response, reverse("assistant:summaries"))
        alice = SummarySettings.objects.get(member=self.alice)
        self.assertFalse(alice.evening_enabled)
        self.assertEqual(alice.evening_time, datetime.time(19, 45))
        self.assertEqual(alice.weekly_time, datetime.time(10, 15))
        self.assertFalse(SummarySettings.objects.filter(member=self.bob).exists())

    def test_invalid_time_is_rejected_without_500(self) -> None:
        self.client.force_login(self.alice)

        response = self.client.post(
            reverse("assistant:summaries"),
            {"evening_time": "25:99", "weekly_time": "21:00"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Введите правильное время")
        self.assertEqual(
            SummarySettings.objects.get(member=self.alice).evening_time,
            datetime.time(21, 0),
        )

    def test_other_member_settings_are_untouched(self) -> None:
        other = SummarySettings.for_member(self.bob)
        other.evening_time = datetime.time(18, 0)
        other.evening_enabled = False
        other.save()
        self.client.force_login(self.alice)

        self.client.post(
            reverse("assistant:summaries"),
            {"evening_time": "20:00", "evening_enabled": "on", "weekly_enabled": "on"},
        )

        bob = SummarySettings.objects.get(member=self.bob)
        self.assertFalse(bob.evening_enabled)
        self.assertEqual(bob.evening_time, datetime.time(18, 0))

    def test_summary_screen_mentions_local_time_and_immutability(self) -> None:
        self.client.force_login(self.alice)

        response = self.client.get(reverse("assistant:summaries"))

        self.assertContains(response, "Время местное")
        self.assertContains(response, "Отправленный текст не меняется")
