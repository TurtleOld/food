import datetime
import json
import re
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import DiaryEntry, Product
from core.tests.test_undo import token_of

User = get_user_model()
HTMX = {"HX-Request": "true"}


def make_product(author, name, calories="100.0"):
    return Product.objects.create(
        name=name,
        base_unit=Product.BaseUnit.GRAM,
        calories=Decimal(calories),
        proteins=Decimal("10.0"),
        fats=Decimal("5.0"),
        carbs=Decimal("20.0"),
        author=author,
    )


class SheetAddBase(TestCase):
    def setUp(self):
        self.member = User.objects.create_user(username="alice", password="s3cret-pass")
        self.other = User.objects.create_user(username="bob", password="s3cret-pass")
        self.client.force_login(self.member)
        self.today = datetime.date.today()
        self.create_url = reverse("core:entry_create", args=[self.today.isoformat()])
        self.search_url = reverse("core:entry_search", args=[self.today.isoformat()])
        self.preview_url = reverse("core:entry_draft_preview", args=[self.today.isoformat()])
        self.chicken = make_product(self.member, "Курица", "165.0")
        self.buckwheat = make_product(self.member, "Гречка варёная", "110.0")

    def eat(self, member, product, amount, meal=DiaryEntry.MealType.LUNCH):
        return DiaryEntry.objects.create(
            member=member, date=self.today, meal_type=meal, product=product, amount=Decimal(amount)
        )


class SheetEntryPointTests(SheetAddBase):
    def checked_meal(self, response):
        match = re.search(r'value="(\w+)"[^>]*checked', response.content.decode())
        return match.group(1) if match else None

    def test_day_page_opens_the_sheet_from_the_prompt_and_from_each_meal(self):
        response = self.client.get(reverse("core:day"))

        self.assertContains(response, f'hx-get="{self.create_url}"')
        self.assertContains(response, 'hx-vals="{&quot;meal&quot;: &quot;dinner&quot;}"')

    def test_without_htmx_the_create_url_is_still_the_plain_page(self):
        response = self.client.get(self.create_url)

        self.assertContains(response, "<html")
        self.assertContains(response, 'name="product"')

    def test_with_htmx_the_create_url_is_the_search_step(self):
        response = self.client.get(self.create_url, headers=HTMX)

        self.assertNotContains(response, "<html")
        self.assertContains(response, 'id="search-results"')
        self.assertContains(response, "autofocus")

    def test_search_step_lists_recent_with_last_amount_when_query_is_empty(self):
        self.eat(self.member, self.chicken, "175")

        response = self.client.get(self.create_url, headers=HTMX)

        self.assertContains(response, "Недавние")
        self.assertContains(response, "Курица")
        self.assertContains(response, "175")

    def test_meal_from_the_card_is_carried_into_the_amount_step(self):
        response = self.client.get(
            self.create_url,
            {"product": self.chicken.pk, "meal": "afternoon_snack"},
            headers=HTMX,
        )

        self.assertEqual(self.checked_meal(response), "afternoon_snack")

    def test_meal_defaults_from_the_users_local_hour(self):
        expected = {
            9: "breakfast",
            10: "second_breakfast",
            11: "second_breakfast",
            12: "lunch",
            14: "lunch",
            15: "afternoon_snack",
            17: "afternoon_snack",
            18: "dinner",
            23: "dinner",
            0: "breakfast",
        }
        for hour, meal in expected.items():
            with self.subTest(hour=hour):
                response = self.client.get(
                    self.create_url, {"product": self.chicken.pk, "hour": hour}, headers=HTMX
                )
                self.assertEqual(self.checked_meal(response), meal)


class SheetSearchTests(SheetAddBase):
    def test_recent_shows_only_own_entries_latest_first(self):
        self.eat(self.member, self.chicken, "100")
        self.eat(self.member, self.buckwheat, "150")
        stranger = make_product(self.other, "Чужой йогурт")
        self.eat(self.other, stranger, "200")

        response = self.client.get(self.search_url, headers=HTMX)

        body = response.content.decode()
        self.assertNotContains(response, "Чужой йогурт")
        self.assertLess(body.index("Гречка"), body.index("Курица"))

    def test_recent_lists_a_product_once_with_its_last_amount(self):
        self.eat(self.member, self.chicken, "100")
        self.eat(self.member, self.chicken, "230")

        response = self.client.get(self.search_url, headers=HTMX)

        self.assertContains(response, "Курица", count=1)
        self.assertContains(response, "230")
        self.assertNotContains(response, "100 г")

    def test_search_matches_substring_ignoring_case(self):
        response = self.client.get(self.search_url, {"q": "ГРЕЧ"}, headers=HTMX)

        self.assertContains(response, "Гречка варёная")
        self.assertNotContains(response, "Курица")
        self.assertNotContains(response, "Недавние")

    def test_search_finds_products_nobody_ate_including_other_authors(self):
        make_product(self.other, "Греческий йогурт")

        response = self.client.get(self.search_url, {"q": "греч"}, headers=HTMX)

        self.assertContains(response, "Греческий йогурт")

    def test_search_result_leads_to_the_amount_step_with_the_meal(self):
        response = self.client.get(self.search_url, {"q": "кур", "meal": "dinner"}, headers=HTMX)

        self.assertContains(response, f"product={self.chicken.pk}")
        self.assertContains(response, "meal=dinner")

    def test_search_without_matches_says_so(self):
        response = self.client.get(self.search_url, {"q": "zzz"}, headers=HTMX)

        self.assertContains(response, "Ничего не найдено")

    def test_search_requires_login_with_hx_redirect(self):
        self.client.logout()

        response = self.client.get(self.search_url, headers=HTMX)

        self.assertIn("HX-Redirect", response)


class SheetAmountStepTests(SheetAddBase):
    def test_amount_is_prefilled_with_the_members_last_amount(self):
        self.eat(self.member, self.chicken, "175")
        self.eat(self.other, self.chicken, "999")

        response = self.client.get(self.create_url, {"product": self.chicken.pk}, headers=HTMX)

        self.assertContains(response, 'value="175"')

    def test_amount_defaults_to_100_without_history(self):
        self.eat(self.other, self.chicken, "999")

        response = self.client.get(self.create_url, {"product": self.chicken.pk}, headers=HTMX)

        self.assertContains(response, 'value="100"')
        self.assertContains(response, "Добавить в")

    def test_unknown_product_is_not_found(self):
        response = self.client.get(self.create_url, {"product": 9999}, headers=HTMX)

        self.assertEqual(response.status_code, 404)

    def test_draft_preview_scales_the_catalog_macros(self):
        response = self.client.get(
            self.preview_url, {"product": self.chicken.pk, "amount": "200"}, headers=HTMX
        )

        self.assertContains(response, 'id="macros"')
        self.assertContains(response, "330")

    def test_draft_preview_with_invalid_amount_shows_no_numbers(self):
        response = self.client.get(
            self.preview_url, {"product": self.chicken.pk, "amount": "abc"}, headers=HTMX
        )

        self.assertContains(response, 'id="macros"')
        self.assertNotContains(response, "165")


class SheetCreateTests(SheetAddBase):
    def setUp(self):
        super().setUp()
        self.valid = {
            "product": self.chicken.pk,
            "meal_type": "dinner",
            "date": self.today.isoformat(),
            "amount": "150",
        }

    def test_htmx_post_creates_the_entry_and_returns_feed_with_toast(self):
        response = self.client.post(self.create_url, self.valid, headers=HTMX)

        entry = DiaryEntry.objects.get()
        self.assertEqual(entry.member, self.member)
        self.assertEqual(entry.amount, Decimal("150.0"))
        self.assertEqual(entry.meal_type, "dinner")
        self.assertContains(response, 'id="feed"')
        self.assertNotContains(response, "<html")
        self.assertContains(response, "Курица, 150 г → Ужин")
        self.assertContains(response, "Вернуть")
        self.assertIn("sheet:close", json.loads(response["HX-Trigger"]))
        self.assertEqual(response["HX-Retarget"], "#feed")

    def test_feed_shows_the_day_the_sheet_was_opened_from(self):
        tomorrow = self.today + datetime.timedelta(days=1)

        response = self.client.post(
            self.create_url, {**self.valid, "date": tomorrow.isoformat()}, headers=HTMX
        )

        self.assertEqual(DiaryEntry.objects.get().date, tomorrow)
        self.assertNotContains(response, "Курица, 150 г</span>")
        self.assertNotContains(response, 'class="entry"')

    def test_undo_from_the_toast_deletes_only_the_created_entry(self):
        keep = self.eat(self.member, self.buckwheat, "80")
        response = self.client.post(self.create_url, self.valid, headers=HTMX)
        token = token_of(response)

        undo = self.client.post(reverse("core:undo"), {"token": token}, headers=HTMX)

        self.assertContains(undo, "Возвращено")
        self.assertEqual(list(DiaryEntry.objects.all()), [keep])

    def test_undo_twice_says_it_is_too_late(self):
        response = self.client.post(self.create_url, self.valid, headers=HTMX)
        token = token_of(response)
        self.client.post(reverse("core:undo"), {"token": token}, headers=HTMX)

        again = self.client.post(reverse("core:undo"), {"token": token}, headers=HTMX)

        self.assertContains(again, "Уже нельзя вернуть")

    def test_invalid_amount_rerenders_the_amount_step_with_errors(self):
        response = self.client.post(self.create_url, {**self.valid, "amount": "0"}, headers=HTMX)

        self.assertContains(response, "Количество должно быть больше нуля")
        self.assertNotContains(response, "<html")
        self.assertNotIn("HX-Trigger", response)
        self.assertFalse(DiaryEntry.objects.exists())

    def test_post_without_htmx_keeps_the_old_redirect_flow(self):
        response = self.client.post(
            self.create_url, {"meal_type": "lunch", "product": self.chicken.pk, "amount": "150"}
        )

        self.assertRedirects(response, reverse("core:day_on", args=[self.today.isoformat()]))
        self.assertEqual(DiaryEntry.objects.count(), 1)


class SheetBadInputTests(SheetAddBase):
    def test_garbage_product_is_not_found_not_a_server_error(self):
        response = self.client.get(self.create_url, {"product": "abc"}, headers=HTMX)

        self.assertEqual(response.status_code, 404)

    def test_post_with_unknown_product_rerenders_with_error(self):
        data = {"product": 9999, "meal_type": "lunch", "date": self.today, "amount": "100"}

        response = self.client.post(self.create_url, data, headers=HTMX)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(DiaryEntry.objects.exists())

    def test_garbage_hour_falls_back_to_server_time(self):
        response = self.client.get(
            self.create_url, {"product": self.chicken.pk, "hour": "x"}, headers=HTMX
        )

        self.assertEqual(response.status_code, 200)

    def test_preview_of_unknown_product_is_not_found(self):
        response = self.client.get(self.preview_url, {"product": 9999}, headers=HTMX)

        self.assertEqual(response.status_code, 404)
