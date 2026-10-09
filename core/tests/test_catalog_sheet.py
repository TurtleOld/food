import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import DiaryEntry, Product
from core.tests.test_sheet_add import HTMX, make_product
from core.tests.test_undo import token_of

User = get_user_model()

VALID = {
    "name": "Овсянка",
    "base_unit": "g",
    "calories": "342",
    "proteins": "12.3",
    "fats": "6.1",
    "carbs": "59.5",
}


class CatalogBase(TestCase):
    def setUp(self):
        self.member = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.force_login(self.member)
        Product.objects.all().delete()
        self.list_url = reverse("core:product_list")
        self.create_url = reverse("core:product_create")
        self.chicken = make_product(self.member, "Курица", "165.0")
        self.buckwheat = make_product(self.member, "Гречка варёная", "110.0")

    def edit_url(self, product):
        return reverse("core:product_edit", args=[product.pk])

    def delete_url(self, product):
        return reverse("core:product_delete", args=[product.pk])

    def eat(self, product):
        return DiaryEntry.objects.create(
            member=self.member,
            date=datetime.date.today(),
            meal_type=DiaryEntry.MealType.LUNCH,
            product=product,
            amount=Decimal("100"),
        )


class CatalogListTests(CatalogBase):
    def test_page_shows_count_rows_with_macros_and_kcal_per_100(self):
        response = self.client.get(self.list_url)

        self.assertContains(response, "2 продукта")
        self.assertContains(response, "Курица")
        self.assertContains(response, "165")
        self.assertContains(response, "/ 100 г")

    def test_millilitre_product_shows_per_100_ml(self):
        Product.objects.create(
            name="Молоко",
            base_unit="ml",
            calories=Decimal("60"),
            proteins=Decimal("3"),
            fats=Decimal("3"),
            carbs=Decimal("5"),
            author=self.member,
        )

        self.assertContains(self.client.get(self.list_url), "/ 100 мл")

    def test_search_is_a_case_insensitive_substring_fragment(self):
        response = self.client.get(self.list_url, {"q": "ГРЕЧ"}, headers=HTMX)

        self.assertNotContains(response, "<html")
        self.assertContains(response, "Гречка варёная")
        self.assertNotContains(response, "Курица")
        self.assertContains(response, 'id="catalog"')

    def test_empty_search_offers_a_new_product_with_the_query(self):
        response = self.client.get(self.list_url, {"q": "тофу"}, headers=HTMX)

        self.assertContains(response, "Ничего не нашлось")
        self.assertContains(response, "+ Новый продукт «тофу»")


class CatalogSheetCrudTests(CatalogBase):
    def test_new_form_opens_in_the_sheet_with_the_query_as_name(self):
        response = self.client.get(self.create_url, {"name": "овсян"}, headers=HTMX)

        self.assertNotContains(response, "<html")
        self.assertContains(response, 'value="овсян"')
        self.assertContains(response, 'name="base_unit"')

    def test_create_returns_list_fragment_toast_and_closes_the_sheet(self):
        response = self.client.post(self.create_url, VALID, headers=HTMX)

        product = Product.objects.get(name="Овсянка")
        self.assertEqual(product.author, self.member)
        self.assertContains(response, 'id="catalog"')
        self.assertContains(response, "Овсянка")
        self.assertContains(response, "Продукт создан")
        self.assertEqual(response["HX-Retarget"], "#catalog")
        self.assertIn("sheet:close", response["HX-Trigger"])

    def test_invalid_create_redisplays_the_form_in_the_sheet(self):
        response = self.client.post(self.create_url, {**VALID, "calories": ""}, headers=HTMX)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "help is-danger")
        self.assertNotIn("HX-Retarget", response)
        self.assertFalse(Product.objects.filter(name="Овсянка").exists())

    def test_edit_form_locks_the_unit_and_shows_author_and_usage(self):
        self.eat(self.chicken)
        self.eat(self.chicken)

        response = self.client.get(self.edit_url(self.chicken), headers=HTMX)

        self.assertNotContains(response, "<html")
        self.assertNotContains(response, 'name="base_unit"')
        self.assertContains(response, "в граммах — единица не меняется после создания")
        self.assertContains(response, "Добавил(а) alice · в 2 записях дневника")

    def test_edit_saves_via_the_sheet_and_keeps_the_unit(self):
        data = {**VALID, "name": "Курица гриль", "base_unit": "ml"}
        response = self.client.post(self.edit_url(self.chicken), data, headers=HTMX)

        self.chicken.refresh_from_db()
        self.assertEqual(self.chicken.name, "Курица гриль")
        self.assertEqual(self.chicken.base_unit, "g")
        self.assertContains(response, "Курица гриль")
        self.assertContains(response, "Продукт обновлён")
        self.assertIn("sheet:close", response["HX-Trigger"])

    def test_unused_product_has_a_delete_button_used_one_an_explanation(self):
        unused = self.client.get(self.edit_url(self.chicken), headers=HTMX)
        self.assertContains(unused, "Удалить продукт")

        self.eat(self.chicken)
        used = self.client.get(self.edit_url(self.chicken), headers=HTMX)
        self.assertNotContains(used, "Удалить продукт")
        self.assertContains(used, "Удалить нельзя — продукт есть в записях дневника")


class CatalogDeleteTests(CatalogBase):
    def test_delete_from_the_sheet_is_immediate_and_offers_undo(self):
        response = self.client.post(self.delete_url(self.chicken), headers=HTMX)

        self.assertFalse(Product.objects.filter(pk=self.chicken.pk).exists())
        self.assertContains(response, "Продукт удалён")
        self.assertContains(response, "Вернуть")
        self.assertNotContains(response, "Курица")
        self.assertIn("sheet:close", response["HX-Trigger"])

    def test_undo_restores_the_deleted_product(self):
        response = self.client.post(self.delete_url(self.chicken), headers=HTMX)
        token = token_of(response)

        undone = self.client.post(reverse("core:undo"), {"token": token}, headers=HTMX)

        restored = Product.objects.get(name="Курица")
        self.assertEqual(restored.pk, self.chicken.pk)
        self.assertEqual(restored.calories, Decimal("165.0"))
        self.assertEqual(restored.author, self.member)
        self.assertContains(undone, "Возвращено")
        self.assertIn("catalog:changed", undone["HX-Trigger"])

    def test_used_product_cannot_be_deleted_even_by_a_direct_request(self):
        self.eat(self.chicken)

        response = self.client.post(self.delete_url(self.chicken), headers=HTMX)

        self.assertTrue(Product.objects.filter(pk=self.chicken.pk).exists())
        self.assertContains(response, "Удалить нельзя")
        self.assertNotContains(response, "Вернуть")
        self.assertEqual(response["HX-Reswap"], "none")

    def test_page_without_js_still_deletes_with_confirmation_page(self):
        page = self.client.get(self.delete_url(self.chicken))
        self.assertContains(page, "Удалить")

        self.client.post(self.delete_url(self.chicken))
        self.assertFalse(Product.objects.filter(pk=self.chicken.pk).exists())
