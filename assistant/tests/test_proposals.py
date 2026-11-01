import datetime
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from assistant.models import Proposal
from assistant.proposals import (
    ProposalDecided,
    ProposalError,
    ProposalNotFound,
    confirm_proposal,
    create_proposal,
    reject_proposal,
    similar_products,
    update_pending_proposal,
)
from core.models import DiaryEntry, Product

User = get_user_model()


class CreateProposalTest(TestCase):
    def setUp(self):
        self.member = User.objects.create_user(username="alice", password="s3cret-pass")
        self.product = Product.objects.get(name="Молоко 3.2%")

    def _create(self, **overrides):
        values = {
            "amount": "150",
            "date": "2026-10-31",
            "meal_type": "lunch",
            "product_id": self.product.pk,
        }
        values.update(overrides)
        return create_proposal(self.member, **values)

    def test_existing_product_proposal_is_pending_and_creates_nothing_else(self):
        products_before = Product.objects.count()

        proposal = self._create()

        self.assertEqual(proposal.status, Proposal.Status.PENDING)
        self.assertEqual(proposal.member, self.member)
        self.assertEqual(proposal.product, self.product)
        self.assertEqual(proposal.amount, Decimal("150.0"))
        self.assertEqual(proposal.date, datetime.date(2026, 10, 31))
        self.assertEqual(proposal.meal_type, DiaryEntry.MealType.LUNCH)
        self.assertEqual(DiaryEntry.objects.count(), 0)
        self.assertEqual(Product.objects.count(), products_before)

    def test_new_product_proposal_keeps_nutrition_without_creating_product(self):
        products_before = Product.objects.count()

        proposal = self._create(
            product_id=None,
            new_product={
                "name": "Печенье овсяное",
                "base_unit": "g",
                "calories": "420",
                "proteins": "7.5",
                "fats": "16",
                "carbs": "63",
            },
        )

        self.assertIsNone(proposal.product)
        self.assertEqual(proposal.new_name, "Печенье овсяное")
        self.assertEqual(proposal.new_base_unit, "g")
        self.assertEqual(proposal.new_calories, Decimal("420.0"))
        self.assertEqual(Product.objects.count(), products_before)

    def test_both_or_neither_product_source_is_rejected(self):
        with self.assertRaisesMessage(ProposalError, "либо Продукт Каталога"):
            self._create(
                new_product={
                    "name": "X",
                    "base_unit": "g",
                    "calories": "1",
                    "proteins": "1",
                    "fats": "1",
                    "carbs": "1",
                }
            )
        with self.assertRaisesMessage(ProposalError, "Укажите Продукт Каталога"):
            self._create(product_id=None)

    def test_unknown_product_is_rejected(self):
        for product_id in (999999, "abc"):
            with (
                self.subTest(product_id=product_id),
                self.assertRaisesMessage(ProposalError, "не найден"),
            ):
                self._create(product_id=product_id)

    def test_bad_amount_is_rejected(self):
        for amount in ("0", "-5", "0.04", "10001", "abc", True, None):
            with self.subTest(amount=amount), self.assertRaises(ProposalError):
                self._create(amount=amount)

    def test_bad_date_and_meal_are_rejected(self):
        with self.assertRaisesMessage(ProposalError, "Дата"):
            self._create(date="31.10.2026")
        with self.assertRaisesMessage(ProposalError, "Приём пищи"):
            self._create(meal_type="supper")

    def test_date_object_and_datetime_are_accepted(self):
        on_date = self._create(date=datetime.date(2026, 10, 30))
        on_datetime = self._create(date=datetime.datetime(2026, 10, 29, 12, 0))

        self.assertEqual(on_date.date, datetime.date(2026, 10, 30))
        self.assertEqual(on_datetime.date, datetime.date(2026, 10, 29))

    def test_new_product_requires_complete_valid_nutrition(self):
        base = {
            "name": "Печенье",
            "base_unit": "g",
            "calories": "420",
            "proteins": "7.5",
            "fats": "16",
            "carbs": "63",
        }
        for field, value in (
            ("name", " "),
            ("base_unit", "kg"),
            ("calories", None),
            ("proteins", "-1"),
            ("fats", "abc"),
            ("carbs", "999999.95"),
        ):
            with self.subTest(field=field), self.assertRaises(ProposalError):
                self._create(product_id=None, new_product={**base, field: value})
        with self.assertRaisesMessage(ProposalError, "длинное"):
            self._create(product_id=None, new_product={**base, "name": "я" * 201})

    def test_similar_products_are_read_only(self):
        before = Product.objects.values_list("pk", "calories")

        similar = similar_products("  молоко  ")

        self.assertEqual([product.name for product in similar], ["Молоко 3.2%"])
        self.assertEqual(list(Product.objects.values_list("pk", "calories")), list(before))
        self.assertEqual(similar_products(""), [])

    def test_similar_products_stop_at_the_limit(self):
        Product.objects.bulk_create(
            Product(
                name=f"Похожий продукт {index}",
                base_unit="g",
                calories=Decimal("100"),
                proteins=Decimal("1"),
                fats=Decimal("1"),
                carbs=Decimal("1"),
                author=self.member,
            )
            for index in range(6)
        )

        self.assertEqual(len(similar_products("Похожий продукт")), 5)

    def test_str_names_the_product_and_state(self):
        proposal = self._create()

        self.assertEqual(str(proposal), "Молоко 3.2%: Ожидает решения")


class ConfirmRejectTest(TestCase):
    def setUp(self):
        self.member = User.objects.create_user(username="alice", password="s3cret-pass")
        self.other = User.objects.create_user(username="bob", password="s3cret-pass")
        self.product = Product.objects.get(name="Молоко 3.2%")

    def _proposal(self, **overrides):
        values = {
            "amount": "200",
            "date": "2026-10-31",
            "meal_type": "breakfast",
            "product_id": self.product.pk,
        }
        values.update(overrides)
        return create_proposal(self.member, **values)

    def test_confirm_existing_product_creates_one_entry_with_snapshot(self):
        proposal = self._proposal()

        confirmed = confirm_proposal(self.member, proposal.pk)

        entry = DiaryEntry.objects.get()
        self.assertEqual(confirmed.status, Proposal.Status.CONFIRMED)
        self.assertEqual(confirmed.entry, entry)
        self.assertIsNotNone(confirmed.decided_at)
        self.assertEqual(entry.member, self.member)
        self.assertEqual(entry.product, self.product)
        self.assertEqual(entry.amount, Decimal("200.0"))
        self.assertEqual(entry.date, datetime.date(2026, 10, 31))
        self.assertEqual(entry.meal_type, DiaryEntry.MealType.BREAKFAST)
        self.assertEqual(entry.calories_snapshot, self.product.calories)

    def test_confirm_new_product_creates_product_and_entry_atomically(self):
        proposal = self._proposal(
            product_id=None,
            new_product={
                "name": "Печенье овсяное",
                "base_unit": "g",
                "calories": "420",
                "proteins": "7.5",
                "fats": "16",
                "carbs": "63",
            },
        )

        confirmed = confirm_proposal(self.member, proposal.pk)

        product = Product.objects.get(name="Печенье овсяное")
        entry = DiaryEntry.objects.get()
        self.assertEqual(product.author, self.member)
        self.assertEqual(product.calories, Decimal("420.0"))
        self.assertEqual(confirmed.product, product)
        self.assertEqual(entry.product, product)
        self.assertEqual(entry.proteins_snapshot, Decimal("7.5"))

    def test_confirm_is_idempotent(self):
        proposal = self._proposal()

        first = confirm_proposal(self.member, proposal.pk)
        second = confirm_proposal(self.member, proposal.pk)

        self.assertEqual(first.entry_id, second.entry_id)
        self.assertEqual(DiaryEntry.objects.count(), 1)

    def test_new_product_rolls_back_when_entry_creation_fails(self):
        proposal = self._proposal(
            product_id=None,
            new_product={
                "name": "Печенье овсяное",
                "base_unit": "g",
                "calories": "420",
                "proteins": "7.5",
                "fats": "16",
                "carbs": "63",
            },
        )

        with (
            mock.patch.object(DiaryEntry.objects, "create", side_effect=RuntimeError),
            self.assertRaises(RuntimeError),
        ):
            confirm_proposal(self.member, proposal.pk)

        self.assertFalse(Product.objects.filter(name="Печенье овсяное").exists())
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, Proposal.Status.PENDING)

    def test_confirm_rejected_proposal_creates_nothing(self):
        proposal = self._proposal()
        reject_proposal(self.member, proposal.pk)

        with self.assertRaisesMessage(ProposalDecided, "отклонено"):
            confirm_proposal(self.member, proposal.pk)

        self.assertEqual(DiaryEntry.objects.count(), 0)

    def test_reject_creates_nothing_and_is_idempotent(self):
        proposal = self._proposal()

        rejected = reject_proposal(self.member, proposal.pk)
        again = reject_proposal(self.member, proposal.pk)

        self.assertEqual(rejected.status, Proposal.Status.REJECTED)
        self.assertEqual(again.status, Proposal.Status.REJECTED)
        self.assertIsNotNone(again.decided_at)
        self.assertEqual(DiaryEntry.objects.count(), 0)

    def test_reject_after_confirm_keeps_the_entry(self):
        proposal = self._proposal()
        confirm_proposal(self.member, proposal.pk)

        with self.assertRaisesMessage(ProposalDecided, "подтверждено"):
            reject_proposal(self.member, proposal.pk)

        self.assertEqual(DiaryEntry.objects.count(), 1)

    def test_confirm_refuses_new_product_without_nutrition(self):
        proposal = Proposal.objects.create(
            member=self.member,
            date=datetime.date(2026, 10, 31),
            meal_type=DiaryEntry.MealType.LUNCH,
            amount=Decimal("100"),
            new_name="Неполный продукт",
            new_base_unit="g",
        )

        with self.assertRaisesMessage(ProposalError, "не хватает КБЖУ"):
            confirm_proposal(self.member, proposal.pk)

        self.assertFalse(Product.objects.filter(name="Неполный продукт").exists())
        self.assertEqual(DiaryEntry.objects.count(), 0)

    def test_other_member_cannot_decide_someone_elses_proposal(self):
        proposal = self._proposal()

        with self.assertRaises(ProposalNotFound):
            confirm_proposal(self.other, proposal.pk)
        with self.assertRaises(ProposalNotFound):
            reject_proposal(self.other, proposal.pk)

        proposal.refresh_from_db()
        self.assertEqual(proposal.status, Proposal.Status.PENDING)
        self.assertEqual(DiaryEntry.objects.count(), 0)

    def test_stale_edit_cannot_overwrite_a_confirmed_proposal(self):
        proposal = self._proposal()
        confirm_proposal(self.member, proposal.pk)
        values = {
            "product": self.product,
            "new_name": "",
            "new_base_unit": "",
            "new_calories": None,
            "new_proteins": None,
            "new_fats": None,
            "new_carbs": None,
            "amount": Decimal("50"),
            "date": datetime.date(2026, 11, 1),
            "meal_type": DiaryEntry.MealType.DINNER,
        }

        with self.assertRaises(ProposalNotFound):
            update_pending_proposal(self.member, proposal.pk, values)

        proposal.refresh_from_db()
        self.assertEqual(proposal.status, Proposal.Status.CONFIRMED)
        self.assertEqual(proposal.amount, Decimal("200.0"))
        self.assertIsNotNone(proposal.entry)
