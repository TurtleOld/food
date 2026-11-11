import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import DailyTarget, DiaryEntry, Product

User = get_user_model()


class DailyTargetEditTest(TestCase):
    def setUp(self):
        self.member = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.force_login(self.member)

    def _edit_url(self):
        return reverse("core:daily_target_edit")

    def test_target_is_created_for_the_signed_in_member(self):
        response = self.client.post(
            self._edit_url(),
            {"calories": "2000", "proteins": "120", "fats": "70", "carbs": "220"},
            follow=True,
        )

        self.assertRedirects(response, reverse("core:day"))
        target = DailyTarget.objects.get()
        self.assertEqual(target.member, self.member)
        self.assertEqual(target.calories, Decimal("2000.0"))
        self.assertEqual(target.proteins, Decimal("120.0"))
        self.assertEqual(target.fats, Decimal("70.0"))
        self.assertEqual(target.carbs, Decimal("220.0"))

    def test_existing_target_is_updated_not_duplicated(self):
        DailyTarget.objects.create(
            member=self.member,
            calories=Decimal("2000"),
            proteins=Decimal("120"),
            fats=Decimal("70"),
            carbs=Decimal("220"),
        )

        self.client.post(
            self._edit_url(),
            {"calories": "1800", "proteins": "100", "fats": "60", "carbs": "200"},
        )

        self.assertEqual(DailyTarget.objects.count(), 1)
        target = DailyTarget.objects.get()
        self.assertEqual(target.calories, Decimal("1800.0"))

    def test_zero_value_is_rejected(self):
        response = self.client.post(
            self._edit_url(),
            {"calories": "0", "proteins": "120", "fats": "70", "carbs": "220"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "больше нуля")
        self.assertFalse(DailyTarget.objects.exists())

    def test_comma_decimals_are_accepted_like_the_rest_of_the_ui(self):
        self.client.post(
            self._edit_url(),
            {"calories": "2100,5", "proteins": "120,5", "fats": "70", "carbs": "220"},
        )

        target = DailyTarget.objects.get()
        self.assertEqual(target.calories, Decimal("2100.5"))
        self.assertEqual(target.proteins, Decimal("120.5"))

    def test_negative_value_is_rejected(self):
        response = self.client.post(
            self._edit_url(),
            {"calories": "2000", "proteins": "-5", "fats": "70", "carbs": "220"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "больше нуля")
        self.assertFalse(DailyTarget.objects.exists())


class DailyTargetProgressTest(TestCase):
    def setUp(self):
        self.member = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.force_login(self.member)
        self.chicken = Product.objects.create(
            name="Куриная грудка",
            base_unit=Product.BaseUnit.GRAM,
            calories=Decimal("165.0"),
            proteins=Decimal("31.0"),
            fats=Decimal("3.6"),
            carbs=Decimal("0.0"),
            author=self.member,
        )

    def test_day_page_without_target_prompts_to_set_one(self):
        response = self.client.get(reverse("core:day"))

        self.assertContains(response, "Цель не задана")

    def test_day_page_shows_target_and_deviation_from_totals(self):
        DailyTarget.objects.create(
            member=self.member,
            calories=Decimal("2000"),
            proteins=Decimal("120"),
            fats=Decimal("70"),
            carbs=Decimal("220"),
        )
        DiaryEntry.objects.create(
            member=self.member,
            date=datetime.date.today(),
            meal_type=DiaryEntry.MealType.LUNCH,
            product=self.chicken,
            amount=Decimal("200"),
        )

        response = self.client.get(reverse("core:day"))

        progress = response.context["progress"]
        self.assertEqual(progress["calories"], Decimal("330.0") - Decimal("2000"))
        self.assertEqual(progress["proteins"], Decimal("62.0") - Decimal("120"))

    def test_other_members_target_is_not_shown(self):
        other = User.objects.create_user(username="bob", password="bob-pass")
        DailyTarget.objects.create(
            member=other,
            calories=Decimal("2000"),
            proteins=Decimal("120"),
            fats=Decimal("70"),
            carbs=Decimal("220"),
        )

        response = self.client.get(reverse("core:day"))

        self.assertContains(response, "Цель не задана")

    def test_visiting_target_form_prefills_existing_values(self):
        DailyTarget.objects.create(
            member=self.member,
            calories=Decimal("2000"),
            proteins=Decimal("120"),
            fats=Decimal("70"),
            carbs=Decimal("220"),
        )

        response = self.client.get(reverse("core:daily_target_edit"))

        self.assertContains(response, "2000,0")


class DailyTargetPageTest(TestCase):
    def setUp(self):
        self.member = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.force_login(self.member)

    def test_page_without_target_explains_what_it_enables(self):
        response = self.client.get(reverse("core:daily_target_edit"))

        self.assertContains(response, "Задайте — и на странице дня появятся кольца")

    def test_page_with_target_hides_the_explanation(self):
        DailyTarget.objects.create(
            member=self.member,
            calories=Decimal("2000"),
            proteins=Decimal("120"),
            fats=Decimal("70"),
            carbs=Decimal("220"),
        )

        response = self.client.get(reverse("core:daily_target_edit"))

        self.assertNotContains(response, "появятся кольца")

    def test_page_shows_kcal_implied_by_macros(self):
        DailyTarget.objects.create(
            member=self.member,
            calories=Decimal("2000"),
            proteins=Decimal("120"),
            fats=Decimal("70"),
            carbs=Decimal("220"),
        )

        response = self.client.get(reverse("core:daily_target_edit"))

        self.assertContains(response, "По БЖУ выходит ≈")
        self.assertContains(response, "1990")
        self.assertContains(response, "подставить")

    def test_save_shows_toast_on_day_page(self):
        response = self.client.post(
            reverse("core:daily_target_edit"),
            {"calories": "2000", "proteins": "120", "fats": "70", "carbs": "220"},
            follow=True,
        )

        self.assertContains(response, "Цель сохранена")


class DailyTargetSheetTest(TestCase):
    """Цель правится из шторки на странице дня, а не уводит на отдельную страницу."""

    def setUp(self):
        self.member = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.force_login(self.member)
        self.url = reverse("core:daily_target_edit")

    def test_htmx_open_returns_only_the_form_fragment(self):
        response = self.client.get(self.url, HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="id_calories"')
        self.assertContains(response, "Суточная цель")
        self.assertContains(response, "hx-post=")
        self.assertNotContains(response, "<html")
        self.assertNotContains(response, ">Отмена<")

    def test_htmx_save_updates_target_and_redirects_the_day_page(self):
        response = self.client.post(
            self.url,
            {"calories": "2100", "proteins": "125", "fats": "72", "carbs": "230"},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response["HX-Redirect"], reverse("core:day"))
        target = DailyTarget.objects.get(member=self.member)
        self.assertEqual(target.calories, Decimal("2100.0"))

    def test_htmx_validation_errors_return_the_form_fragment(self):
        response = self.client.post(
            self.url,
            {"calories": "0", "proteins": "125", "fats": "72", "carbs": "230"},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "больше нуля")
        self.assertFalse(DailyTarget.objects.exists())
