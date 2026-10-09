import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from core.diary import day_summary
from core.models import DailyTarget, DiaryEntry, Product

User = get_user_model()
DAY = datetime.date(2026, 3, 10)


class DaySummaryTest(TestCase):
    def setUp(self):
        self.member = User.objects.create_user(username="alice", password="s3cret-pass")
        self.product = Product.objects.create(
            name="Овсянка",
            base_unit=Product.BaseUnit.GRAM,
            calories=Decimal("350"),
            proteins=Decimal("12"),
            fats=Decimal("6"),
            carbs=Decimal("60"),
            author=self.member,
        )

    def add_entry(self, meal_type, amount, date=DAY):
        return DiaryEntry.objects.create(
            member=self.member,
            date=date,
            meal_type=meal_type,
            product=self.product,
            amount=Decimal(amount),
        )

    def test_empty_day_has_no_meals_zero_totals_and_no_progress(self):
        summary = day_summary(self.member, DAY)

        self.assertEqual(summary.meals, [])
        self.assertEqual(summary.totals["calories"], Decimal(0))
        self.assertEqual(summary.totals["carbs"], Decimal(0))
        self.assertIsNone(summary.progress)

    def test_meal_and_day_totals_come_from_snapshots(self):
        self.add_entry(DiaryEntry.MealType.BREAKFAST, "200")
        self.add_entry(DiaryEntry.MealType.BREAKFAST, "100")
        self.add_entry(DiaryEntry.MealType.DINNER, "100")
        self.add_entry(DiaryEntry.MealType.LUNCH, "500", date=DAY + datetime.timedelta(days=1))
        self.product.calories = Decimal("999")
        self.product.save()

        summary = day_summary(self.member, DAY)

        self.assertEqual(
            [meal["type"] for meal in summary.meals],
            [DiaryEntry.MealType.BREAKFAST, DiaryEntry.MealType.DINNER],
        )
        breakfast = summary.meals[0]
        self.assertEqual(len(breakfast["entries"]), 2)
        self.assertEqual(breakfast["calories"], Decimal("1050"))
        self.assertEqual(breakfast["proteins"], Decimal("36"))
        self.assertEqual(summary.totals["calories"], Decimal("1400"))
        self.assertEqual(summary.totals["fats"], Decimal("24"))

    def test_other_members_entries_are_excluded(self):
        bob = User.objects.create_user(username="bob", password="s3cret-pass")
        DiaryEntry.objects.create(
            member=bob,
            date=DAY,
            meal_type=DiaryEntry.MealType.LUNCH,
            product=self.product,
            amount=Decimal("100"),
        )

        self.assertEqual(day_summary(self.member, DAY).meals, [])

    def test_progress_is_totals_minus_target(self):
        self.add_entry(DiaryEntry.MealType.LUNCH, "100")
        target = DailyTarget.objects.create(
            member=self.member,
            calories=Decimal("2000"),
            proteins=Decimal("10"),
            fats=Decimal("50"),
            carbs=Decimal("60"),
        )

        progress = day_summary(self.member, DAY).progress

        assert progress is not None

        self.assertEqual(progress["target"], target)
        self.assertEqual(progress["calories"], Decimal("-1650"))
        self.assertEqual(progress["proteins"], Decimal("2"))
        self.assertEqual(progress["carbs"], Decimal("0"))
