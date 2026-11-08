import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import DiaryEntry, Product

User = get_user_model()
HTMX = {"HX-Request": "true"}


class LongProductNameTest(TestCase):
    """Продукт с названием в 200 символов без пробелов остаётся читаемым и рабочим."""

    def setUp(self):
        self.member = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.force_login(self.member)
        self.long_name = ("Оченьдлинноеназваниепродуктабезпробелов" * 6)[:200]
        self.assertEqual(len(self.long_name), 200)
        self.product = Product.objects.create(
            name=self.long_name,
            base_unit=Product.BaseUnit.GRAM,
            calories=Decimal("250.0"),
            proteins=Decimal("10.0"),
            fats=Decimal("5.0"),
            carbs=Decimal("30.0"),
            author=self.member,
        )
        self.today = datetime.date.today()
        self.create_url = reverse("core:entry_create", args=[self.today.isoformat()])

    def test_member_adds_and_edits_an_entry_with_a_long_name(self):
        created = self.client.post(
            self.create_url,
            {
                "product": self.product.pk,
                "meal_type": "lunch",
                "date": self.today.isoformat(),
                "amount": "150",
            },
            headers=HTMX,
        )

        self.assertEqual(created.status_code, 200)
        self.assertContains(created, self.long_name)
        self.assertContains(created, "150,0 г")
        entry = DiaryEntry.objects.get()

        edit_url = reverse("core:entry_edit", args=[entry.pk])
        sheet = self.client.get(edit_url, headers=HTMX)
        self.assertContains(sheet, self.long_name)

        edited = self.client.post(
            edit_url,
            {"date": self.today, "meal_type": "dinner", "amount": "250"},
            headers=HTMX,
        )
        self.assertEqual(edited.status_code, 200)
        self.assertContains(edited, self.long_name)
        self.assertContains(edited, "250,0 г")
        entry.refresh_from_db()
        self.assertEqual(entry.amount, Decimal("250.0"))

    def test_long_name_is_whole_on_the_day_page_and_in_recent(self):
        DiaryEntry.objects.create(
            member=self.member,
            date=self.today,
            meal_type=DiaryEntry.MealType.BREAKFAST,
            product=self.product,
            amount=Decimal("250"),
        )

        day = self.client.get(reverse("core:day"))
        self.assertContains(day, self.long_name)

        recent = self.client.get(self.create_url, headers=HTMX)
        self.assertContains(recent, self.long_name)
        self.assertContains(recent, "250 г")

    def test_long_name_is_whole_in_product_search_results(self):
        search_url = reverse("core:entry_search", args=[self.today.isoformat()])

        response = self.client.get(search_url, {"q": "Оченьдлинное"}, headers=HTMX)

        self.assertContains(response, self.long_name)
