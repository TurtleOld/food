import datetime
import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import DailyTarget, DiaryEntry, Product

User = get_user_model()
HTMX = {"HX-Request": "true"}


class SheetEditTests(TestCase):
    """Правка записи из шторки: тот же URL отдаёт страницу или фрагмент."""

    def setUp(self):
        self.member = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.force_login(self.member)
        self.chicken = Product.objects.create(
            name="Курица",
            base_unit=Product.BaseUnit.GRAM,
            calories=Decimal("165.0"),
            proteins=Decimal("31.0"),
            fats=Decimal("3.6"),
            carbs=Decimal("0.0"),
            author=self.member,
        )
        DailyTarget.objects.create(
            member=self.member,
            calories=Decimal("2000"),
            proteins=Decimal("100"),
            fats=Decimal("70"),
            carbs=Decimal("250"),
        )
        self.today = datetime.date.today()
        self.entry = DiaryEntry.objects.create(
            member=self.member,
            date=self.today,
            meal_type=DiaryEntry.MealType.LUNCH,
            product=self.chicken,
            amount=Decimal("100"),
        )
        self.edit_url = reverse("core:entry_edit", args=[self.entry.pk])
        self.valid = {"date": self.today, "meal_type": "dinner", "amount": "200"}

    def test_edit_page_without_htmx_is_a_full_page(self):
        response = self.client.get(self.edit_url)

        self.assertContains(response, "<html")
        self.assertContains(response, 'id="form"')

    def test_edit_with_htmx_is_the_form_fragment_only(self):
        response = self.client.get(self.edit_url, headers=HTMX)

        self.assertNotContains(response, "<html")
        self.assertContains(response, 'id="form"')
        self.assertContains(response, "Курица")

    def test_fragment_offers_quick_amounts_and_last_amount_of_the_product(self):
        DiaryEntry.objects.create(
            member=self.member,
            date=self.today,
            meal_type=DiaryEntry.MealType.DINNER,
            product=self.chicken,
            amount=Decimal("175"),
        )

        response = self.client.get(self.edit_url, headers=HTMX)

        for amount in (50, 100, 150, 200, 175):
            self.assertContains(response, f'data-amount="{amount}"')

    def test_valid_htmx_post_saves_and_returns_feed_with_oob_toast(self):
        response = self.client.post(self.edit_url, self.valid, headers=HTMX)

        self.assertEqual(response.status_code, 200)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.amount, Decimal("200.0"))
        self.assertEqual(self.entry.meal_type, DiaryEntry.MealType.DINNER)
        self.assertContains(response, 'id="feed"')
        self.assertContains(response, "Ужин")
        self.assertNotContains(response, "<html")
        self.assertContains(response, 'hx-swap-oob="beforeend:#toasts"')
        self.assertContains(response, "Запись обновлена")

    def test_valid_htmx_post_closes_the_sheet_and_retargets_the_feed(self):
        response = self.client.post(self.edit_url, self.valid, headers=HTMX)

        self.assertIn("sheet:close", json.loads(response["HX-Trigger"]))
        self.assertEqual(response["HX-Retarget"], "#feed")
        self.assertEqual(response["HX-Reswap"], "outerHTML")

    def test_feed_after_moving_entry_shows_the_day_it_was_opened_from(self):
        tomorrow = self.today + datetime.timedelta(days=1)

        response = self.client.post(self.edit_url, {**self.valid, "date": tomorrow}, headers=HTMX)

        self.assertNotContains(response, "Курица")

    def test_valid_post_without_htmx_redirects_to_the_day(self):
        response = self.client.post(self.edit_url, self.valid)

        self.assertRedirects(response, reverse("core:day_on", args=[self.today.isoformat()]))

    def test_invalid_htmx_post_rerenders_the_form_fragment_with_errors(self):
        response = self.client.post(self.edit_url, {**self.valid, "amount": "0"}, headers=HTMX)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="form"')
        self.assertContains(response, "Количество должно быть больше нуля")
        self.assertNotContains(response, "<html")
        self.assertNotIn("HX-Trigger", response)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.amount, Decimal("100.0"))

    def test_day_page_entry_opens_the_edit_fragment_into_the_sheet(self):
        response = self.client.get(reverse("core:day"))

        self.assertContains(response, f'hx-get="{self.edit_url}"')
        self.assertContains(response, 'id="sheet-body"')

    def test_day_fragment_is_the_feed_only(self):
        response = self.client.get(reverse("core:day"), headers=HTMX)

        self.assertContains(response, 'id="feed"')
        self.assertNotContains(response, "<html")


class SheetPreviewTests(TestCase):
    """Живой пересчёт КБЖУ на сервере по снапшоту записи."""

    def setUp(self):
        self.member = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.force_login(self.member)
        self.product = Product.objects.create(
            name="Курица",
            base_unit=Product.BaseUnit.GRAM,
            calories=Decimal("165.0"),
            proteins=Decimal("31.0"),
            fats=Decimal("3.6"),
            carbs=Decimal("0.0"),
            author=self.member,
        )
        self.entry = DiaryEntry.objects.create(
            member=self.member,
            date=datetime.date.today(),
            meal_type=DiaryEntry.MealType.LUNCH,
            product=self.product,
            amount=Decimal("100"),
        )
        self.url = reverse("core:entry_preview", args=[self.entry.pk])

    def test_preview_scales_the_entry_snapshot_not_the_catalog(self):
        self.product.calories = Decimal("999.0")
        self.product.save()

        response = self.client.get(self.url, {"amount": "200"}, headers=HTMX)

        self.assertContains(response, 'id="macros"')
        self.assertContains(response, "330")
        self.assertNotContains(response, "<html")
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.amount, Decimal("100.0"))

    def test_preview_with_invalid_amount_shows_no_numbers(self):
        response = self.client.get(self.url, {"amount": "abc"}, headers=HTMX)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="macros"')
        self.assertNotContains(response, "165")

    def test_preview_of_another_members_entry_is_not_found(self):
        other = User.objects.create_user(username="bob", password="s3cret-pass")
        self.client.force_login(other)

        response = self.client.get(self.url, {"amount": "200"}, headers=HTMX)

        self.assertEqual(response.status_code, 404)


class SessionLossTests(TestCase):
    def setUp(self):
        self.url = reverse("core:day")

    def test_htmx_request_without_session_gets_hx_redirect_to_login_with_next(self):
        response = self.client.get(
            self.url, headers={**HTMX, "HX-Current-URL": "http://testserver/day/2026-01-02/"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["HX-Redirect"], f"{reverse('core:login')}?next=/day/2026-01-02/")

    def test_plain_request_without_session_gets_302_to_login(self):
        response = self.client.get(self.url)

        self.assertRedirects(response, f"{reverse('core:login')}?next={self.url}")
