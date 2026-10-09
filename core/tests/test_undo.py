import datetime
import re
import time
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import DiaryEntry, Product

User = get_user_model()
HTMX = {"HX-Request": "true"}


def token_of(response) -> str:
    match = re.search(r'name="token" value="([^"]+)"', response.content.decode())
    assert match, "в ответе нет тоста с «Вернуть»"
    return match.group(1)


class UndoTests(TestCase):
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
        self.today = datetime.date.today()
        self.entry = DiaryEntry.objects.create(
            member=self.member,
            date=self.today,
            meal_type=DiaryEntry.MealType.LUNCH,
            product=self.product,
            amount=Decimal("100"),
        )
        self.delete_url = reverse("core:entry_delete", args=[self.entry.pk])
        self.undo_url = reverse("core:undo")

    def delete_via_sheet(self):
        return self.client.post(self.delete_url, headers=HTMX)

    def test_htmx_delete_removes_entry_and_returns_feed_with_undo_toast(self):
        response = self.delete_via_sheet()

        self.assertEqual(response.status_code, 200)
        self.assertFalse(DiaryEntry.objects.filter(pk=self.entry.pk).exists())
        self.assertContains(response, 'id="feed"')
        self.assertNotContains(response, "Курица")
        self.assertContains(response, "Запись удалена")
        self.assertContains(response, "Вернуть")
        self.assertEqual(response["HX-Retarget"], "#feed")
        self.assertIn("sheet:close", response["HX-Trigger"])

    def test_undo_recreates_entry_with_new_pk_and_same_snapshot(self):
        token = token_of(self.delete_via_sheet())
        self.product.calories = Decimal("999.0")
        self.product.save()

        response = self.client.post(self.undo_url, {"token": token}, headers=HTMX)

        restored = DiaryEntry.objects.get()
        self.assertNotEqual(restored.pk, self.entry.pk)
        self.assertEqual(restored.product, self.product)
        self.assertEqual(restored.amount, Decimal("100.0"))
        self.assertEqual(restored.meal_type, DiaryEntry.MealType.LUNCH)
        self.assertEqual(restored.date, self.today)
        self.assertEqual(restored.calories_snapshot, Decimal("165.0"))
        self.assertEqual(restored.member, self.member)
        self.assertContains(response, 'id="feed"')
        self.assertContains(response, "Курица")
        self.assertEqual(response["HX-Retarget"], "#feed")

    def test_undo_after_edit_restores_previous_amount_meal_and_day(self):
        tomorrow = self.today + datetime.timedelta(days=1)
        response = self.client.post(
            reverse("core:entry_edit", args=[self.entry.pk]),
            {"date": tomorrow, "meal_type": "dinner", "amount": "250"},
            headers=HTMX,
        )

        self.client.post(self.undo_url, {"token": token_of(response)}, headers=HTMX)

        self.entry.refresh_from_db()
        self.assertEqual(self.entry.amount, Decimal("100.0"))
        self.assertEqual(self.entry.meal_type, DiaryEntry.MealType.LUNCH)
        self.assertEqual(self.entry.date, self.today)
        self.assertEqual(DiaryEntry.objects.count(), 1)

    def test_expired_token_gives_a_toast_not_an_error(self):
        token = token_of(self.delete_via_sheet())

        with mock.patch("time.time", return_value=time.time() + 301):
            response = self.client.post(self.undo_url, {"token": token}, headers=HTMX)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Уже нельзя вернуть")
        self.assertFalse(DiaryEntry.objects.exists())

    def test_token_is_still_valid_just_before_five_minutes(self):
        token = token_of(self.delete_via_sheet())

        with mock.patch("time.time", return_value=time.time() + 299):
            self.client.post(self.undo_url, {"token": token}, headers=HTMX)

        self.assertTrue(DiaryEntry.objects.exists())

    def test_forged_token_gives_a_toast_not_an_error(self):
        token = token_of(self.delete_via_sheet())

        response = self.client.post(self.undo_url, {"token": token + "x"}, headers=HTMX)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Уже нельзя вернуть")
        self.assertFalse(DiaryEntry.objects.exists())

    def test_missing_token_gives_a_toast(self):
        response = self.client.post(self.undo_url, {}, headers=HTMX)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Уже нельзя вернуть")

    def test_another_members_token_does_nothing(self):
        token = token_of(self.delete_via_sheet())
        bob = User.objects.create_user(username="bob", password="s3cret-pass")
        self.client.force_login(bob)

        response = self.client.post(self.undo_url, {"token": token}, headers=HTMX)

        self.assertContains(response, "Уже нельзя вернуть")
        self.assertFalse(DiaryEntry.objects.exists())

    def test_undo_requires_login(self):
        self.client.logout()

        response = self.client.post(self.undo_url, {"token": "x"})

        self.assertEqual(response.status_code, 302)

    def test_plain_undo_post_redirects_to_the_day(self):
        token = token_of(self.delete_via_sheet())

        response = self.client.post(self.undo_url, {"token": token})

        self.assertRedirects(response, reverse("core:day_on", args=[self.today.isoformat()]))
        self.assertTrue(DiaryEntry.objects.exists())

    def test_plain_delete_still_goes_through_confirmation_without_undo(self):
        page = self.client.get(self.delete_url)
        self.assertContains(page, "Удалить")

        response = self.client.post(self.delete_url, follow=True)

        self.assertNotContains(response, 'name="token"')
        self.assertContains(response, "Запись удалена")

    def test_edit_sheet_offers_delete_via_htmx_post(self):
        response = self.client.get(reverse("core:entry_edit", args=[self.entry.pk]), headers=HTMX)

        self.assertContains(response, f'hx-post="{self.delete_url}"')
        self.assertContains(response, "Удалить запись")

    def current_day(self, day):
        return {**HTMX, "HX-Current-URL": f"http://testserver/day/{day.isoformat()}/"}

    def test_undo_of_edit_that_moved_the_day_renders_the_day_on_the_page(self):
        tomorrow = self.today + datetime.timedelta(days=1)
        response = self.client.post(
            reverse("core:entry_edit", args=[self.entry.pk]),
            {"date": tomorrow, "meal_type": "dinner", "amount": "250"},
            headers=HTMX,
        )
        DiaryEntry.objects.create(
            member=self.member,
            date=tomorrow,
            meal_type=DiaryEntry.MealType.DINNER,
            product=Product.objects.create(
                name="Гречка",
                base_unit=Product.BaseUnit.GRAM,
                calories=Decimal("110.0"),
                proteins=Decimal("4.0"),
                fats=Decimal("1.0"),
                carbs=Decimal("20.0"),
                author=self.member,
            ),
            amount=Decimal("50"),
        )

        undone = self.client.post(
            self.undo_url, {"token": token_of(response)}, headers=self.current_day(tomorrow)
        )

        self.assertContains(undone, "Гречка")
        self.assertNotContains(undone, "Курица")

    def test_undo_outside_a_day_page_falls_back_to_the_restored_day(self):
        token = token_of(self.delete_via_sheet())

        undone = self.client.post(
            self.undo_url,
            {"token": token},
            headers={**HTMX, "HX-Current-URL": "http://testserver/products/"},
        )

        self.assertContains(undone, "Курица")

    def test_undo_on_the_today_page_renders_today(self):
        token = token_of(self.delete_via_sheet())

        undone = self.client.post(
            self.undo_url,
            {"token": token},
            headers={**HTMX, "HX-Current-URL": "http://testserver/"},
        )

        self.assertContains(undone, "Курица")

    def test_plain_edit_without_htmx_offers_no_undo(self):
        response = self.client.post(
            reverse("core:entry_edit", args=[self.entry.pk]),
            {"date": self.today, "meal_type": "dinner", "amount": "250"},
            follow=True,
        )

        self.assertContains(response, "Запись обновлена")
        self.assertNotContains(response, 'name="token"')

    def test_undo_of_product_deletion_with_deleted_author_is_refused_not_500(self):
        bob = User.objects.create_user(username="bob", password="s3cret-pass")
        product = Product.objects.create(
            name="Тофу",
            base_unit=Product.BaseUnit.GRAM,
            calories=Decimal("76.0"),
            proteins=Decimal("8.0"),
            fats=Decimal("4.0"),
            carbs=Decimal("2.0"),
            author=bob,
        )
        token = token_of(
            self.client.post(reverse("core:product_delete", args=[product.pk]), headers=HTMX)
        )
        bob.delete()

        undone = self.client.post(self.undo_url, {"token": token}, headers=HTMX)

        self.assertContains(undone, "Уже нельзя вернуть")
        self.assertFalse(Product.objects.filter(name="Тофу").exists())
