import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import DiaryEntry, Product

User = get_user_model()


class DiaryEntryCreateTest(TestCase):
    def setUp(self):
        self.member = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.force_login(self.member)
        self.product = Product.objects.create(
            name="Гречка варёная",
            base_unit=Product.BaseUnit.GRAM,
            calories=Decimal("110.0"),
            proteins=Decimal("4.2"),
            fats=Decimal("1.1"),
            carbs=Decimal("21.0"),
            author=self.member,
        )
        self.today = datetime.date.today()

    def _create_url(self, date=None):
        return reverse("core:entry_create", args=[(date or self.today).isoformat()])

    def test_entry_is_added_to_chosen_meal(self):
        response = self.client.post(
            self._create_url(),
            {"meal_type": "lunch", "product": self.product.pk, "amount": "150"},
            follow=True,
        )

        self.assertRedirects(response, reverse("core:day_on", args=[self.today.isoformat()]))
        entry = DiaryEntry.objects.get()
        self.assertEqual(entry.member, self.member)
        self.assertEqual(entry.meal_type, DiaryEntry.MealType.LUNCH)
        self.assertEqual(entry.product, self.product)
        self.assertEqual(entry.amount, Decimal("150.0"))

    def test_entry_snapshots_nutrition_at_creation_time(self):
        self.client.post(
            self._create_url(),
            {"meal_type": "lunch", "product": self.product.pk, "amount": "200"},
        )

        entry = DiaryEntry.objects.get()
        self.assertEqual(entry.calories_snapshot, Decimal("110.0"))
        self.assertEqual(entry.proteins_snapshot, Decimal("4.2"))
        self.assertEqual(entry.fats_snapshot, Decimal("1.1"))
        self.assertEqual(entry.carbs_snapshot, Decimal("21.0"))

    def test_entry_totals_are_computed_from_snapshot_and_amount(self):
        self.client.post(
            self._create_url(),
            {"meal_type": "lunch", "product": self.product.pk, "amount": "200"},
        )

        entry = DiaryEntry.objects.get()
        self.assertEqual(entry.calories, Decimal("220.0"))
        self.assertEqual(entry.proteins, Decimal("8.4"))
        self.assertEqual(entry.fats, Decimal("2.2"))
        self.assertEqual(entry.carbs, Decimal("42.0"))

    def test_editing_product_does_not_change_past_entries(self):
        self.client.post(
            self._create_url(),
            {"meal_type": "lunch", "product": self.product.pk, "amount": "100"},
        )
        entry = DiaryEntry.objects.get()

        self.product.calories = Decimal("999.0")
        self.product.save()
        entry.refresh_from_db()

        self.assertEqual(entry.calories_snapshot, Decimal("110.0"))
        self.assertEqual(entry.calories, Decimal("110.0"))

    def test_zero_amount_is_rejected(self):
        response = self.client.post(
            self._create_url(),
            {"meal_type": "lunch", "product": self.product.pk, "amount": "0"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "больше нуля")
        self.assertFalse(DiaryEntry.objects.exists())

    def test_negative_amount_is_rejected(self):
        response = self.client.post(
            self._create_url(),
            {"meal_type": "lunch", "product": self.product.pk, "amount": "-5"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "больше нуля")
        self.assertFalse(DiaryEntry.objects.exists())

    def test_amount_above_limit_is_rejected(self):
        response = self.client.post(
            self._create_url(),
            {"meal_type": "lunch", "product": self.product.pk, "amount": "10001"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "не может быть больше")
        self.assertFalse(DiaryEntry.objects.exists())

    def test_amount_at_limit_is_accepted(self):
        response = self.client.post(
            self._create_url(),
            {"meal_type": "lunch", "product": self.product.pk, "amount": "10000"},
            follow=True,
        )

        self.assertRedirects(response, reverse("core:day_on", args=[self.today.isoformat()]))
        self.assertTrue(DiaryEntry.objects.exists())

    def test_entry_can_be_created_for_a_past_date(self):
        past_date = self.today - datetime.timedelta(days=3)

        response = self.client.post(
            self._create_url(date=past_date),
            {"meal_type": "lunch", "product": self.product.pk, "amount": "100"},
            follow=True,
        )

        self.assertRedirects(response, reverse("core:day_on", args=[past_date.isoformat()]))
        entry = DiaryEntry.objects.get()
        self.assertEqual(entry.date, past_date)


class DayPageTotalsTest(TestCase):
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
        self.rice = Product.objects.create(
            name="Рис белый варёный",
            base_unit=Product.BaseUnit.GRAM,
            calories=Decimal("130.0"),
            proteins=Decimal("2.7"),
            fats=Decimal("0.3"),
            carbs=Decimal("28.0"),
            author=self.member,
        )
        self.today = datetime.date.today()

    def test_empty_day_shows_every_meal_as_quiet_add_row(self):
        response = self.client.get(reverse("core:day"))

        self.assertContains(response, "· добавить", count=5)

    def test_meal_and_day_totals_match_sum_of_entries(self):
        DiaryEntry.objects.create(
            member=self.member,
            date=self.today,
            meal_type=DiaryEntry.MealType.LUNCH,
            product=self.chicken,
            amount=Decimal("200"),
        )
        DiaryEntry.objects.create(
            member=self.member,
            date=self.today,
            meal_type=DiaryEntry.MealType.LUNCH,
            product=self.rice,
            amount=Decimal("150"),
        )

        response = self.client.get(reverse("core:day"))

        self.assertContains(response, "· добавить", count=4)
        meal = response.context["meals"][0]
        self.assertEqual(meal["calories"], Decimal("525.0"))
        self.assertEqual(response.context["totals"]["calories"], Decimal("525.0"))

    def test_only_current_members_entries_are_shown(self):
        other = User.objects.create_user(username="bob", password="bob-pass")
        DiaryEntry.objects.create(
            member=other,
            date=self.today,
            meal_type=DiaryEntry.MealType.DINNER,
            product=self.chicken,
            amount=Decimal("100"),
        )

        response = self.client.get(reverse("core:day"))

        self.assertContains(response, "· добавить", count=5)

    def test_navigating_to_next_and_previous_day(self):
        response = self.client.get(reverse("core:day"))

        previous_url = reverse(
            "core:day_on", args=[(self.today - datetime.timedelta(days=1)).isoformat()]
        )
        next_url = reverse(
            "core:day_on", args=[(self.today + datetime.timedelta(days=1)).isoformat()]
        )
        self.assertContains(response, previous_url)
        self.assertContains(response, next_url)

    def test_calories_are_whole_and_macros_have_one_decimal(self):
        DiaryEntry.objects.create(
            member=self.member,
            date=self.today,
            meal_type=DiaryEntry.MealType.LUNCH,
            product=self.chicken,
            amount=Decimal("100"),
        )

        response = self.client.get(reverse("core:day"))

        self.assertContains(response, "165 <small")
        self.assertContains(response, "Б 31.0")
        self.assertContains(response, "Ж 3.6")
        self.assertContains(response, "У 0.0")


class DiaryEntryEditTest(TestCase):
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
        self.today = datetime.date.today()
        self.entry = DiaryEntry.objects.create(
            member=self.member,
            date=self.today,
            meal_type=DiaryEntry.MealType.LUNCH,
            product=self.chicken,
            amount=Decimal("100"),
        )

    def _edit_url(self, entry=None):
        return reverse("core:entry_edit", args=[(entry or self.entry).pk])

    def test_editing_amount_recomputes_totals_from_snapshot(self):
        response = self.client.post(
            self._edit_url(),
            {"date": self.today, "meal_type": "lunch", "amount": "200"},
            follow=True,
        )

        self.assertRedirects(response, reverse("core:day_on", args=[self.today.isoformat()]))
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.amount, Decimal("200.0"))
        self.assertEqual(self.entry.calories, Decimal("330.0"))

    def test_editing_meal_type_moves_entry_to_another_meal(self):
        self.client.post(
            self._edit_url(),
            {"date": self.today, "meal_type": "dinner", "amount": "100"},
        )

        self.entry.refresh_from_db()
        self.assertEqual(self.entry.meal_type, DiaryEntry.MealType.DINNER)

    def test_editing_date_moves_entry_to_another_day(self):
        new_date = self.today + datetime.timedelta(days=1)

        response = self.client.post(
            self._edit_url(),
            {"date": new_date, "meal_type": "lunch", "amount": "100"},
            follow=True,
        )

        self.assertRedirects(response, reverse("core:day_on", args=[new_date.isoformat()]))
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.date, new_date)

    def test_editing_catalog_product_does_not_change_existing_entries(self):
        self.chicken.calories = Decimal("999.0")
        self.chicken.save()

        self.entry.refresh_from_db()

        self.assertEqual(self.entry.calories_snapshot, Decimal("165.0"))
        self.assertEqual(self.entry.calories, Decimal("165.0"))

    def test_cannot_edit_another_members_entry(self):
        other = User.objects.create_user(username="bob", password="bob-pass")
        other_entry = DiaryEntry.objects.create(
            member=other,
            date=self.today,
            meal_type=DiaryEntry.MealType.LUNCH,
            product=self.chicken,
            amount=Decimal("100"),
        )

        response = self.client.get(self._edit_url(other_entry))

        self.assertEqual(response.status_code, 404)


class DiaryEntryDeleteTest(TestCase):
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
        self.today = datetime.date.today()
        self.entry = DiaryEntry.objects.create(
            member=self.member,
            date=self.today,
            meal_type=DiaryEntry.MealType.LUNCH,
            product=self.chicken,
            amount=Decimal("100"),
        )

    def _delete_url(self, entry=None):
        return reverse("core:entry_delete", args=[(entry or self.entry).pk])

    def test_deleting_entry_removes_it_from_meal_and_day_totals(self):
        response = self.client.post(self._delete_url(), follow=True)

        self.assertRedirects(response, reverse("core:day_on", args=[self.today.isoformat()]))
        self.assertFalse(DiaryEntry.objects.exists())
        self.assertContains(response, "· добавить", count=5)

    def test_cannot_delete_another_members_entry(self):
        other = User.objects.create_user(username="bob", password="bob-pass")
        other_entry = DiaryEntry.objects.create(
            member=other,
            date=self.today,
            meal_type=DiaryEntry.MealType.LUNCH,
            product=self.chicken,
            amount=Decimal("100"),
        )

        response = self.client.post(reverse("core:entry_delete", args=[other_entry.pk]))

        self.assertEqual(response.status_code, 404)
        self.assertTrue(DiaryEntry.objects.filter(pk=other_entry.pk).exists())
