from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import Product

User = get_user_model()


class ProductCreateTest(TestCase):
    def setUp(self):
        self.member = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.force_login(self.member)

    def test_member_creates_product_with_full_nutrition(self):
        response = self.client.post(
            reverse("core:product_create"),
            {
                "name": "Овсянка",
                "base_unit": Product.BaseUnit.GRAM,
                "calories": "342",
                "proteins": "12.3",
                "fats": "6.1",
                "carbs": "59.5",
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("core:product_list"))
        product = Product.objects.get(name="Овсянка")
        self.assertEqual(product.base_unit, Product.BaseUnit.GRAM)
        self.assertEqual(product.calories, Decimal("342.0"))
        self.assertEqual(product.author, self.member)

    def test_missing_nutrition_field_is_rejected(self):
        response = self.client.post(
            reverse("core:product_create"),
            {
                "name": "Овсянка",
                "base_unit": Product.BaseUnit.GRAM,
                "calories": "",
                "proteins": "12.3",
                "fats": "6.1",
                "carbs": "59.5",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Обязательное поле", status_code=200)
        self.assertFalse(Product.objects.filter(name="Овсянка").exists())

    def test_non_numeric_nutrition_field_is_rejected(self):
        response = self.client.post(
            reverse("core:product_create"),
            {
                "name": "Овсянка",
                "base_unit": Product.BaseUnit.GRAM,
                "calories": "много",
                "proteins": "12.3",
                "fats": "6.1",
                "carbs": "59.5",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Product.objects.filter(name="Овсянка").exists())

    def test_base_unit_must_be_gram_or_milliliter(self):
        response = self.client.post(
            reverse("core:product_create"),
            {
                "name": "Овсянка",
                "base_unit": "kg",
                "calories": "342",
                "proteins": "12.3",
                "fats": "6.1",
                "carbs": "59.5",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Product.objects.filter(name="Овсянка").exists())


class ProductEditTest(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="alice-pass")
        self.bob = User.objects.create_user(username="bob", password="bob-pass")
        self.product = Product.objects.create(
            name="Гречка",
            base_unit=Product.BaseUnit.GRAM,
            calories=Decimal("343.0"),
            proteins=Decimal("13.0"),
            fats=Decimal("3.4"),
            carbs=Decimal("62.1"),
            author=self.alice,
        )

    def test_any_member_can_edit_any_product(self):
        self.client.force_login(self.bob)

        response = self.client.post(
            reverse("core:product_edit", args=[self.product.pk]),
            {
                "name": "Гречка ядрица",
                "base_unit": Product.BaseUnit.GRAM,
                "calories": "343",
                "proteins": "13",
                "fats": "3.4",
                "carbs": "62.1",
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("core:product_list"))
        self.product.refresh_from_db()
        self.assertEqual(self.product.name, "Гречка ядрица")
        self.assertEqual(self.product.author, self.alice)

    def test_base_unit_cannot_be_changed_on_edit(self):
        self.client.force_login(self.bob)

        response = self.client.post(
            reverse("core:product_edit", args=[self.product.pk]),
            {
                "name": "Гречка",
                "base_unit": Product.BaseUnit.MILLILITER,
                "calories": "343",
                "proteins": "13",
                "fats": "3.4",
                "carbs": "62.1",
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("core:product_list"))
        self.product.refresh_from_db()
        self.assertEqual(self.product.base_unit, Product.BaseUnit.GRAM)

    def test_product_author_is_shown_in_list(self):
        self.client.force_login(self.bob)

        response = self.client.get(
            reverse("core:product_edit", args=[self.product.pk]), headers={"HX-Request": "true"}
        )

        self.assertContains(response, "Гречка")
        self.assertContains(response, "alice")


class ProductDeleteTest(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="alice-pass")
        self.bob = User.objects.create_user(username="bob", password="bob-pass")
        self.product = Product.objects.create(
            name="Гречка",
            base_unit=Product.BaseUnit.GRAM,
            calories=Decimal("343.0"),
            proteins=Decimal("13.0"),
            fats=Decimal("3.4"),
            carbs=Decimal("62.1"),
            author=self.alice,
        )

    def test_any_member_can_delete_unreferenced_product(self):
        self.client.force_login(self.bob)

        response = self.client.post(
            reverse("core:product_delete", args=[self.product.pk]),
            follow=True,
        )

        self.assertRedirects(response, reverse("core:product_list"))
        self.assertFalse(Product.objects.filter(pk=self.product.pk).exists())


class ProductSearchTest(TestCase):
    def setUp(self):
        self.member = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.force_login(self.member)
        Product.objects.create(
            name="Куриная грудка",
            base_unit=Product.BaseUnit.GRAM,
            calories=Decimal("165.0"),
            proteins=Decimal("31.0"),
            fats=Decimal("3.6"),
            carbs=Decimal("0.0"),
            author=self.member,
        )
        Product.objects.create(
            name="Молоко",
            base_unit=Product.BaseUnit.MILLILITER,
            calories=Decimal("64.0"),
            proteins=Decimal("3.2"),
            fats=Decimal("3.6"),
            carbs=Decimal("4.8"),
            author=self.member,
        )

    def test_search_is_case_insensitive_substring_match(self):
        response = self.client.get(reverse("core:product_list"), {"q": "курин"})

        self.assertContains(response, "Куриная грудка")
        self.assertNotContains(response, "Молоко")

    def test_search_matches_regardless_of_case(self):
        response = self.client.get(reverse("core:product_list"), {"q": "МОЛОКО"})

        self.assertContains(response, "Молоко")
        self.assertNotContains(response, "Куриная грудка")


class ProductSeedTest(TestCase):
    def test_seed_products_are_loaded_after_migrations(self):
        self.assertGreaterEqual(Product.objects.count(), 20)

    def test_seed_is_idempotent_by_name(self):
        names = list(Product.objects.values_list("name", flat=True))

        self.assertEqual(len(names), len(set(names)))
