import datetime
import re
import secrets
from decimal import Decimal
from unittest import mock

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


class CopyBase(TestCase):
    def setUp(self):
        self.member = User.objects.create_user(username="alice", password="s3cret-pass")
        self.other = User.objects.create_user(username="bob", password="s3cret-pass")
        self.client.force_login(self.member)
        self.source = datetime.date.today() - datetime.timedelta(days=1)
        self.target = datetime.date.today()
        self.chicken = make_product(self.member, "Курица", "165.0")
        self.rice = make_product(self.member, "Рис", "110.0")

    def eat(self, member, product, amount, day, meal=DiaryEntry.MealType.LUNCH):
        return DiaryEntry.objects.create(
            member=member,
            date=day,
            meal_type=meal,
            product=product,
            amount=Decimal(amount),
        )

    def copy_url(self, day=None):
        return reverse("core:diary_copy", args=[(day or self.source).isoformat()])

    def operation(self):
        page = self.client.get(self.copy_url()).content.decode()
        match = re.search(r'name="operation" value="([^"]+)"', page)
        assert match, "в форме копирования нет одноразового ключа"
        return match.group(1)

    def post_copy(
        self,
        *,
        scope="day",
        source=None,
        target=None,
        operation=None,
        meal="",
        entry="",
        target_meal="",
        follow=False,
        **extra,
    ):
        data = {
            "scope": scope,
            "source_date": (source or self.source).isoformat(),
            "target_date": (target or self.target).isoformat(),
            "meal_type": meal,
            "entry": entry,
            "target_meal_type": target_meal,
            "operation": operation or secrets.token_urlsafe(8),
            **extra,
        }
        return self.client.post(self.copy_url(source or self.source), data, follow=follow)


class CopyEntryPointTests(CopyBase):
    def test_day_page_offers_copy_for_the_whole_day_and_each_filled_meal(self):
        self.eat(self.member, self.chicken, "100", self.source)

        page = self.client.get(reverse("core:day_on", args=[self.source.isoformat()]))

        self.assertContains(page, self.copy_url())
        self.assertContains(page, f"{self.copy_url()}?meal=lunch")
        self.assertContains(page, "Копировать")

    def test_copy_page_is_a_plain_page_without_htmx_and_a_sheet_step_with_it(self):
        plain = self.client.get(self.copy_url())
        sheet = self.client.get(self.copy_url(), headers=HTMX)

        self.assertContains(plain, "<html")
        self.assertContains(plain, "Исходный день")
        self.assertNotContains(sheet, "<html")
        self.assertContains(sheet, 'name="source_date"')
        self.assertContains(sheet, "Целевой день")

    def test_entry_edit_sheet_links_to_copying_that_entry(self):
        entry = self.eat(self.member, self.chicken, "100", self.source)

        page = self.client.get(reverse("core:entry_edit", args=[entry.pk]), headers=HTMX)

        self.assertContains(page, f"{self.copy_url()}?entry={entry.pk}")

    def test_meal_copy_page_names_the_chosen_meal(self):
        page = self.client.get(f"{self.copy_url()}?meal=lunch")

        self.assertContains(page, "Обед")

    def test_copy_requires_login(self):
        self.client.logout()

        response = self.client.get(self.copy_url())

        self.assertRedirects(response, f"{reverse('core:login')}?next={self.copy_url()}")


class CopyDayTests(CopyBase):
    def test_whole_day_is_appended_to_the_target_and_existing_entries_stay(self):
        self.eat(self.member, self.chicken, "120", self.source, DiaryEntry.MealType.BREAKFAST)
        self.eat(self.member, self.rice, "150", self.source, DiaryEntry.MealType.LUNCH)
        existing = self.eat(self.member, self.rice, "50", self.target, DiaryEntry.MealType.DINNER)

        response = self.post_copy()

        self.assertRedirects(response, reverse("core:day_on", args=[self.target.isoformat()]))
        target = list(DiaryEntry.objects.filter(member=self.member, date=self.target))
        self.assertEqual(len(target), 3)
        self.assertIn(existing, target)
        copied = [entry for entry in target if entry.pk != existing.pk]
        self.assertCountEqual(
            [(entry.product, entry.amount, entry.meal_type) for entry in copied],
            [
                (self.chicken, Decimal("120"), DiaryEntry.MealType.BREAKFAST),
                (self.rice, Decimal("150"), DiaryEntry.MealType.LUNCH),
            ],
        )
        self.assertEqual(DiaryEntry.objects.filter(member=self.member, date=self.source).count(), 2)

    def test_copy_takes_fresh_catalog_snapshots_and_keeps_source_snapshots(self):
        source_entry = self.eat(self.member, self.chicken, "100", self.source)
        self.chicken.calories = Decimal("200.0")
        self.chicken.save()

        self.post_copy()

        source_entry.refresh_from_db()
        copy = DiaryEntry.objects.get(member=self.member, date=self.target)
        self.assertEqual(source_entry.calories_snapshot, Decimal("165.0"))
        self.assertEqual(copy.calories_snapshot, Decimal("200.0"))

    def test_empty_source_day_is_a_form_error_and_writes_nothing(self):
        response = self.post_copy()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "нет записей")
        self.assertFalse(DiaryEntry.objects.filter(member=self.member, date=self.target).exists())

    def test_invalid_target_date_is_a_form_error_and_writes_nothing(self):
        self.eat(self.member, self.chicken, "100", self.source)

        response = self.client.post(
            self.copy_url(),
            {
                "scope": "day",
                "source_date": self.source.isoformat(),
                "target_date": "31/02/2026",
                "operation": secrets.token_urlsafe(8),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Введите правильную дату")
        self.assertFalse(DiaryEntry.objects.filter(member=self.member, date=self.target).exists())

    def test_unknown_scope_is_a_form_error_not_a_server_error(self):
        self.eat(self.member, self.chicken, "100", self.source)

        response = self.post_copy(scope="everything")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(DiaryEntry.objects.filter(member=self.member, date=self.target).exists())

    def test_same_operation_sent_twice_writes_once(self):
        self.eat(self.member, self.chicken, "100", self.source)
        operation = self.operation()

        first = self.post_copy(operation=operation)
        second = self.post_copy(operation=operation, follow=True)

        self.assertRedirects(first, reverse("core:day_on", args=[self.target.isoformat()]))
        self.assertEqual(DiaryEntry.objects.filter(member=self.member, date=self.target).count(), 1)
        self.assertContains(second, "уже")

    def test_copy_does_not_touch_another_members_source_entries(self):
        self.eat(self.member, self.chicken, "100", self.source)
        self.eat(self.other, self.chicken, "200", self.source)

        self.post_copy()

        copied = DiaryEntry.objects.filter(member=self.member, date=self.target)
        self.assertEqual(copied.count(), 1)
        self.assertEqual(copied.get().amount, Decimal("100"))

    def test_copy_rolls_back_all_entries_when_one_create_fails(self):
        self.eat(self.member, self.chicken, "100", self.source, DiaryEntry.MealType.BREAKFAST)
        self.eat(self.member, self.rice, "150", self.source, DiaryEntry.MealType.LUNCH)
        original_save = DiaryEntry.save
        created = 0

        def flaky_save(entry, *args, **kwargs):
            nonlocal created
            if entry._state.adding:
                created += 1
                if created == 2:
                    raise RuntimeError("сбой записи")
            return original_save(entry, *args, **kwargs)

        with mock.patch.object(DiaryEntry, "save", flaky_save):
            with self.assertRaises(RuntimeError):
                self.post_copy()

        self.assertFalse(DiaryEntry.objects.filter(member=self.member, date=self.target).exists())


class CopyMealTests(CopyBase):
    def test_only_the_chosen_meal_is_copied(self):
        self.eat(self.member, self.chicken, "100", self.source, DiaryEntry.MealType.BREAKFAST)
        self.eat(self.member, self.rice, "150", self.source, DiaryEntry.MealType.LUNCH)

        self.post_copy(scope="meal", meal=DiaryEntry.MealType.LUNCH)

        copied = DiaryEntry.objects.get(member=self.member, date=self.target)
        self.assertEqual(copied.product, self.rice)
        self.assertEqual(copied.meal_type, DiaryEntry.MealType.LUNCH)

    def test_missing_meal_for_meal_scope_is_a_form_error(self):
        self.eat(self.member, self.chicken, "100", self.source)

        response = self.post_copy(scope="meal")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "приём пищи")
        self.assertFalse(DiaryEntry.objects.filter(member=self.member, date=self.target).exists())

    def test_empty_source_meal_says_the_meal_is_empty(self):
        self.eat(self.member, self.chicken, "100", self.source, DiaryEntry.MealType.BREAKFAST)

        response = self.post_copy(scope="meal", meal=DiaryEntry.MealType.LUNCH)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "В выбранном приёме нет записей")
        self.assertFalse(DiaryEntry.objects.filter(member=self.member, date=self.target).exists())


class CopyEntryTests(CopyBase):
    def test_single_entry_goes_to_the_chosen_target_meal(self):
        entry = self.eat(self.member, self.chicken, "120", self.source, DiaryEntry.MealType.LUNCH)

        self.post_copy(
            scope="entry",
            entry=entry.pk,
            target_meal=DiaryEntry.MealType.BREAKFAST,
        )

        copied = DiaryEntry.objects.get(member=self.member, date=self.target)
        self.assertEqual(copied.product, self.chicken)
        self.assertEqual(copied.amount, Decimal("120"))
        self.assertEqual(copied.meal_type, DiaryEntry.MealType.BREAKFAST)

    def test_entry_page_prefills_its_day_and_meal(self):
        entry = self.eat(self.member, self.chicken, "120", self.source, DiaryEntry.MealType.DINNER)

        page = self.client.get(f"{self.copy_url()}?entry={entry.pk}")

        self.assertContains(page, f'value="{entry.pk}"')
        self.assertContains(page, 'value="dinner"')
        self.assertContains(page, "checked")

    def test_another_members_entry_cannot_be_opened_or_copied(self):
        foreign = self.eat(self.other, self.chicken, "120", self.source)

        opened = self.client.get(f"{self.copy_url()}?entry={foreign.pk}")
        posted = self.post_copy(scope="entry", entry=foreign.pk)

        self.assertEqual(opened.status_code, 404)
        self.assertEqual(posted.status_code, 200)
        self.assertFalse(DiaryEntry.objects.filter(member=self.member, date=self.target).exists())


class CopyUndoTests(CopyBase):
    def test_undo_removes_the_whole_copy_and_keeps_entries_that_were_there(self):
        self.eat(self.member, self.chicken, "100", self.source, DiaryEntry.MealType.BREAKFAST)
        self.eat(self.member, self.rice, "150", self.source, DiaryEntry.MealType.LUNCH)
        existing = self.eat(self.member, self.rice, "50", self.target, DiaryEntry.MealType.DINNER)
        copied_page = self.post_copy(follow=True)

        undone = self.client.post(
            reverse("core:undo"),
            {"token": token_of(copied_page)},
            headers={**HTMX, "HX-Current-URL": f"http://testserver/day/{self.target}/"},
        )

        self.assertContains(undone, 'id="feed"')
        self.assertEqual(
            list(DiaryEntry.objects.filter(member=self.member, date=self.target)), [existing]
        )
        self.assertEqual(DiaryEntry.objects.filter(member=self.member, date=self.source).count(), 2)

    def test_undo_refuses_to_remove_only_some_of_the_copy(self):
        self.eat(self.member, self.chicken, "100", self.source, DiaryEntry.MealType.BREAKFAST)
        self.eat(self.member, self.rice, "150", self.source, DiaryEntry.MealType.LUNCH)
        copied_page = self.post_copy(follow=True)
        self.eat(self.member, self.chicken, "100", self.source)
        copied = DiaryEntry.objects.filter(member=self.member, date=self.target).first()
        assert copied is not None
        copied.delete()

        undone = self.client.post(
            reverse("core:undo"), {"token": token_of(copied_page)}, headers=HTMX
        )

        self.assertContains(undone, "Уже нельзя вернуть")
        self.assertEqual(DiaryEntry.objects.filter(member=self.member, date=self.target).count(), 1)

    def test_undo_token_belongs_to_the_member_who_copied(self):
        self.eat(self.member, self.chicken, "100", self.source)
        copied_page = self.post_copy(follow=True)
        self.client.force_login(self.other)

        undone = self.client.post(
            reverse("core:undo"), {"token": token_of(copied_page)}, headers=HTMX
        )

        self.assertContains(undone, "Уже нельзя вернуть")
        self.assertTrue(DiaryEntry.objects.filter(member=self.member, date=self.target).exists())
