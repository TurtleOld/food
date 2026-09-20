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

        self.assertContains(response, "2000.0")
