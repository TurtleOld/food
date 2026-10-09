import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import DailyTarget, DiaryEntry, Product

User = get_user_model()


class DayPageTest(TestCase):
    """Personal day page: the app's entry point behind authentication."""

    def test_anonymous_visitor_is_redirected_to_login(self):
        response = self.client.get(reverse("core:day"))

        self.assertRedirects(response, f"{reverse('core:login')}?next={reverse('core:day')}")

    def test_member_sees_personal_empty_day(self):
        member = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.force_login(member)

        response = self.client.get(reverse("core:day"))

        self.assertContains(response, "alice")
        self.assertContains(response, "· добавить", count=5)

    def test_health_endpoint_is_available(self):
        response = self.client.get("/healthz")

        self.assertEqual(response.status_code, 200)


class DayPageLanguageDTest(TestCase):
    """Day page in language D: rings, five-meal feed, quiet empty meals."""

    def setUp(self):
        self.member = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.force_login(self.member)
        self.today = datetime.date.today()
        self.chicken = Product.objects.create(
            name="Куриная грудка",
            base_unit=Product.BaseUnit.GRAM,
            calories=Decimal("165.0"),
            proteins=Decimal("31.0"),
            fats=Decimal("3.6"),
            carbs=Decimal("0.0"),
            author=self.member,
        )

    def _target(self, calories="2000"):
        DailyTarget.objects.create(
            member=self.member,
            calories=Decimal(calories),
            proteins=Decimal("120"),
            fats=Decimal("70"),
            carbs=Decimal("220"),
        )

    def _eat(self, amount="200", meal_type=DiaryEntry.MealType.LUNCH):
        return DiaryEntry.objects.create(
            member=self.member,
            date=self.today,
            meal_type=meal_type,
            product=self.chicken,
            amount=Decimal(amount),
        )

    def test_all_five_meals_are_listed_and_empty_ones_invite_adding(self):
        self._eat()

        response = self.client.get(reverse("core:day"))

        for label in ("Завтрак", "Второй завтрак", "Полдник", "Ужин"):
            self.assertContains(response, f"{label}</span> <span")
        self.assertContains(response, "· добавить", count=4)

    def test_filled_meal_card_shows_entry_and_meal_total(self):
        self._eat()

        response = self.client.get(reverse("core:day"))

        self.assertContains(response, "Куриная грудка")
        self.assertContains(response, "330 ккал")
        self.assertContains(
            response, 'class="entry-kcal num">330 <small class="muted">ккал</small>'
        )

    def test_day_total_is_shown_with_and_without_target(self):
        self._eat()

        without_target = self.client.get(reverse("core:day"))
        self._target()
        with_target = self.client.get(reverse("core:day"))

        for response in (without_target, with_target):
            self.assertContains(response, "Итого за день:")
            self.assertContains(response, "330 ккал</b>")

    def test_entry_row_is_a_single_link_to_edit_page_without_icons(self):
        entry = self._eat()

        response = self.client.get(reverse("core:day"))

        self.assertContains(
            response, f'href="{reverse("core:entry_edit", args=[entry.pk])}"', count=1
        )
        self.assertNotContains(response, "✎")
        self.assertNotContains(response, reverse("core:entry_delete", args=[entry.pk]))

    def test_what_did_you_eat_row_links_to_add_page(self):
        response = self.client.get(reverse("core:day"))

        self.assertContains(response, "Что съели?")
        self.assertContains(
            response, f'href="{reverse("core:entry_create", args=[self.today.isoformat()])}"'
        )

    def test_rings_show_kcal_of_target_and_macro_goals_in_order(self):
        self._target()
        self._eat()

        response = self.client.get(reverse("core:day"))

        self.assertContains(response, "из 2000 ккал")
        self.assertContains(response, "Осталось <b")
        self.assertContains(response, "1670</b> ккал")
        content = response.content.decode()
        positions = [
            content.index(f"{label} · {goal} г")
            for label, goal in (("Б", 120), ("Ж", 70), ("У", 220))
        ]
        self.assertEqual(positions, sorted(positions))

    def test_over_target_shows_excess_and_danger_ring(self):
        self._target(calories="300")
        self._eat()

        response = self.client.get(reverse("core:day"))

        self.assertContains(response, "Сверх цели на <b")
        self.assertContains(response, "30</b> ккал")
        self.assertContains(response, 'stroke="var(--bulma-danger)"')

    def test_under_target_has_no_danger_ring(self):
        self._target()
        self._eat()

        response = self.client.get(reverse("core:day"))

        self.assertNotContains(response, 'stroke="var(--bulma-danger)"')

    def test_without_target_there_are_no_rings_and_goal_is_suggested(self):
        response = self.client.get(reverse("core:day"))

        self.assertNotContains(response, "pathLength")
        self.assertContains(response, reverse("core:daily_target_edit"))
        self.assertContains(response, "Цель не задана")

    def test_date_input_carries_current_day_and_arrows_switch_days(self):
        day = datetime.date(2026, 3, 10)

        response = self.client.get(reverse("core:day_on", args=[day.isoformat()]))

        self.assertContains(response, "10/03/2026")
        self.assertContains(response, reverse("core:day_on", args=["2026-03-09"]))
        self.assertContains(response, reverse("core:day_on", args=["2026-03-11"]))

    def test_feed_and_summary_share_one_block_with_feed_id(self):
        response = self.client.get(reverse("core:day"))

        self.assertContains(response, 'id="feed"', count=1)
