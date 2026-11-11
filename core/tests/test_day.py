import datetime
import re
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils.formats import date_format

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

    def test_day_total_is_rings_or_one_text_with_mobile_brief(self):
        self._eat()

        without_target = self.client.get(reverse("core:day"))
        self._target()
        with_target = self.client.get(reverse("core:day"))

        self.assertContains(without_target, "330 ккал</b>")
        self.assertContains(without_target, "Б 62,0 г")
        self.assertContains(without_target, "· цель не задана")
        self.assertContains(with_target, 'class="summary summary-brief"')
        self.assertContains(with_target, "330</b> / 2000 ккал")
        self.assertNotContains(with_target, "330 ккал</b>")

    def test_mobile_brief_controls_the_full_summary_and_sits_before_the_feed(self):
        self._target()
        self._eat()

        response = self.client.get(reverse("core:day"))

        content = response.content.decode()
        brief_start = content.index('class="summary summary-brief"')
        summary_start = content.index('id="day-summary"')
        brief = content[brief_start:summary_start]
        summary = content[summary_start : content.index('class="timeline"')]
        self.assertLess(brief_start, summary_start)
        self.assertLess(summary_start, content.index('class="timeline"'))
        self.assertIn('aria-controls="day-summary"', brief)
        self.assertIn("330</b> / 2000 ккал", brief)
        self.assertIn("· осталось 1670 ккал", brief)
        self.assertIn('class="ring"', summary)
        self.assertIn("из 2000 ккал", summary)
        self.assertIn("Осталось <b", summary)
        self.assertIn(reverse("core:daily_target_edit"), summary)

    def test_desktop_summary_keeps_the_rings_and_the_target_action(self):
        self._target()
        self._eat()

        response = self.client.get(reverse("core:day"))

        self.assertContains(
            response, 'id="day-summary" class="box summary summary-full has-text-centered"'
        )
        self.assertContains(response, "из 2000 ккал")
        self.assertContains(response, "Осталось <b")
        self.assertContains(response, "Б · 120,0 г")
        self.assertContains(response, "Ж · 70,0 г")
        self.assertContains(response, "У · 220,0 г")
        self.assertContains(response, "Изменить цель")

    def test_rounding_note_is_a_tooltip_instead_of_permanent_footnote(self):
        self._target()
        self._eat()

        response = self.client.get(reverse("core:day"))

        self.assertNotContains(response, ">Ккал округлены до целых, БЖУ — до 0,1 г.<")
        self.assertContains(response, 'title="Ккал округлены до целых, БЖУ — до 0,1 г."', count=1)

    def test_entry_row_is_a_single_link_to_edit_page_without_icons(self):
        entry = self._eat()

        response = self.client.get(reverse("core:day"))

        self.assertContains(
            response, f'href="{reverse("core:entry_edit", args=[entry.pk])}"', count=1
        )
        self.assertNotContains(response, "✎")
        self.assertNotContains(response, reverse("core:entry_delete", args=[entry.pk]))

    def test_primary_add_action_leads_to_add_page_before_the_summary(self):
        self._target()
        response = self.client.get(reverse("core:day"))

        content = response.content.decode()
        add_url = reverse("core:entry_create", args=[self.today.isoformat()])
        self.assertContains(response, "Добавить запись")
        self.assertContains(response, f'href="{add_url}"')
        self.assertContains(response, f'href="{add_url}?scan=1"')
        self.assertContains(response, 'aria-label="Сканер штрихкода"')
        self.assertLess(content.index("Добавить запись"), content.index("Итого за день:"))
        self.assertLess(content.index("Итого за день:"), content.index('class="ring"'))

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
            for label, goal in (("Б", "120,0"), ("Ж", "70,0"), ("У", "220,0"))
        ]
        self.assertEqual(positions, sorted(positions))

    def test_every_ring_names_its_value_and_daily_goal(self):
        self._target()
        self._eat()

        response = self.client.get(reverse("core:day"))

        for label in (
            "Калории: 330 из 2000 ккал",
            "Белки: 62,0 из 120,0 граммов",
            "Жиры: 7,2 из 70,0 граммов",
            "Углеводы: 0,0 из 220,0 граммов",
        ):
            self.assertContains(response, f'aria-label="{label}"')
        self.assertContains(response, 'class="ring-label" aria-hidden="true"', count=4)

    def test_rings_draw_with_the_shared_nutrient_palette(self):
        self._target()
        self._eat()

        response = self.client.get(reverse("core:day"))

        for color in ("var(--kcal)", "var(--p)", "var(--f)", "var(--c)"):
            self.assertContains(response, f'stroke="{color}"')

    def test_fractional_macros_keep_one_decimal_with_comma_and_grams(self):
        self._target()
        self._eat("150")

        response = self.client.get(reverse("core:day"))

        self.assertContains(response, "Б 46,5 г")
        self.assertContains(response, "Ж 5,4 г")
        self.assertContains(response, "У 0,0 г")
        self.assertContains(response, "150,0 г")
        self.assertNotContains(response, "46.5")
        self.assertNotContains(response, "5.4")

    def test_over_target_shows_excess_and_danger_ring(self):
        self._target(calories="300")
        self._eat()

        response = self.client.get(reverse("core:day"))

        self.assertContains(response, "Сверх цели на <b")
        self.assertContains(response, "30</b> ккал")
        self.assertContains(response, "· сверх на 30 ккал")
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

    def test_day_page_has_one_main_heading_naming_today_and_its_date(self):
        response = self.client.get(reverse("core:day"))

        self.assertContains(response, "<h1", count=1)
        self.assertRegex(
            response.content.decode(),
            r"<h1\b[^>]*>Сегодня<span\b[^>]*>, "
            f"{re.escape(date_format(self.today, 'j E Y'))}</span></h1>",
        )

    def test_past_day_main_heading_names_the_weekday_and_date(self):
        day = datetime.date(2026, 3, 10)

        response = self.client.get(reverse("core:day_on", args=[day.isoformat()]))

        self.assertRegex(
            response.content.decode(),
            rf"<h1\b[^>]*>{re.escape(date_format(day, 'l'))}<span\b[^>]*>, "
            f"{re.escape(date_format(day, 'j E Y'))}</span></h1>",
        )

    def test_every_meal_is_an_h2_heading_on_empty_and_filled_days(self):
        empty = self.client.get(reverse("core:day"))
        self._eat()
        filled = self.client.get(reverse("core:day"))

        for response in (empty, filled):
            self.assertContains(response, "<h2", count=5)

    def test_date_controls_name_the_action_and_the_day_for_assistive_tech(self):
        day = datetime.date(2026, 3, 10)

        response = self.client.get(reverse("core:day_on", args=[day.isoformat()]))

        self.assertContains(response, 'aria-label="Изменить дату: 10/03/2026"')
        self.assertContains(response, 'aria-label="Копировать день"')
        self.assertContains(response, 'aria-label="Предыдущий день"')
        self.assertContains(response, 'aria-label="Следующий день"')

    def test_feed_and_summary_share_one_block_with_feed_id(self):
        response = self.client.get(reverse("core:day"))

        self.assertContains(response, 'id="feed"', count=1)
