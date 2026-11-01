import datetime
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from assistant.models import Proposal
from assistant.proposals import create_product_proposal, create_proposal
from core.models import DiaryEntry, Product

User = get_user_model()

NEW_PRODUCT = {
    "name": "Печенье овсяное",
    "base_unit": "g",
    "calories": "420",
    "proteins": "7.5",
    "fats": "16",
    "carbs": "63",
}


class ProposalViewsTest(TestCase):
    def setUp(self):
        self.member = User.objects.create_user(username="alice", password="s3cret-pass")
        self.other = User.objects.create_user(username="bob", password="s3cret-pass")
        self.product = Product.objects.get(name="Молоко 3.2%")
        self.client.force_login(self.member)

    def _proposal(self, member=None, meal="lunch", **overrides):
        values = {
            "amount": "150",
            "date": "2026-10-31",
            "meal_type": meal,
            "product_id": self.product.pk,
        }
        values.update(overrides)
        return create_proposal(member or self.member, **values)

    def _product_proposal(self, member=None):
        return create_product_proposal(member or self.member, new_product=NEW_PRODUCT)

    def test_list_requires_login(self):
        self.client.logout()

        response = self.client.get(reverse("assistant:proposals"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("core:login"), response["Location"])

    def test_list_shows_only_own_proposals(self):
        self._proposal()
        other_product = Product.objects.create(
            name="Чужой продукт",
            base_unit="g",
            calories=Decimal("100"),
            proteins=Decimal("1"),
            fats=Decimal("1"),
            carbs=Decimal("1"),
            author=self.other,
        )
        create_proposal(
            self.other,
            amount="10",
            date="2026-10-31",
            meal_type="dinner",
            product_id=other_product.pk,
        )

        response = self.client.get(reverse("assistant:proposals"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Молоко 3.2%")
        self.assertNotContains(response, "Чужой продукт")

    def test_edit_form_is_unavailable_for_other_member_and_decided_proposal(self):
        own = self._proposal()
        foreign = self._proposal(member=self.other)
        decided = self._proposal(meal="dinner")
        self.client.post(reverse("assistant:proposal_confirm", args=[decided.pk]))

        self.assertEqual(
            self.client.get(reverse("assistant:proposal_edit", args=[foreign.pk])).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(reverse("assistant:proposal_edit", args=[decided.pk])).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(reverse("assistant:proposal_edit", args=[own.pk])).status_code, 200
        )
        self.assertEqual(
            self.client.get(
                reverse("assistant:proposal_edit", args=[own.pk]), {"product": "not-a-pk"}
            ).status_code,
            200,
        )

    def test_edit_changes_amount_date_meal_and_new_product_nutrition(self):
        proposal = self._proposal(product_id=None, new_product=NEW_PRODUCT)

        response = self.client.post(
            reverse("assistant:proposal_edit", args=[proposal.pk]),
            {
                "product": "",
                "new_name": "Печенье овсяное с изюмом",
                "new_base_unit": "g",
                "new_calories": "400",
                "new_proteins": "8",
                "new_fats": "14",
                "new_carbs": "60",
                "amount": "35",
                "date": "01/11/2026",
                "meal_type": "afternoon_snack",
            },
        )

        self.assertRedirects(response, reverse("assistant:proposals"))
        proposal.refresh_from_db()
        self.assertEqual(proposal.new_name, "Печенье овсяное с изюмом")
        self.assertEqual(proposal.new_calories, Decimal("400.0"))
        self.assertEqual(proposal.amount, Decimal("35.0"))
        self.assertEqual(str(proposal.date), "2026-11-01")
        self.assertEqual(proposal.meal_type, DiaryEntry.MealType.AFTERNOON_SNACK)
        self.assertIsNone(proposal.product)

    def test_edit_to_similar_existing_product_clears_new_product_data(self):
        proposal = self._proposal(product_id=None, new_product=NEW_PRODUCT)

        response = self.client.get(
            reverse("assistant:proposal_edit", args=[proposal.pk]),
            {"product": self.product.pk},
        )
        self.assertEqual(response.status_code, 200)

        self.client.post(
            reverse("assistant:proposal_edit", args=[proposal.pk]),
            {
                "product": self.product.pk,
                "new_name": NEW_PRODUCT["name"],
                "new_base_unit": "g",
                "new_calories": "420",
                "new_proteins": "7.5",
                "new_fats": "16",
                "new_carbs": "63",
                "amount": "200",
                "date": "31/10/2026",
                "meal_type": "lunch",
            },
        )

        proposal.refresh_from_db()
        self.assertEqual(proposal.product, self.product)
        self.assertEqual(proposal.new_name, "")
        self.assertIsNone(proposal.new_calories)

    def test_confirm_creates_one_entry_and_repeat_does_not_duplicate(self):
        proposal = self._proposal()

        response = self.client.post(reverse("assistant:proposal_confirm", args=[proposal.pk]))
        repeat = self.client.post(reverse("assistant:proposal_confirm", args=[proposal.pk]))

        self.assertRedirects(response, reverse("assistant:proposals"))
        self.assertRedirects(repeat, reverse("assistant:proposals"))
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, Proposal.Status.CONFIRMED)
        self.assertEqual(DiaryEntry.objects.count(), 1)
        self.assertEqual(DiaryEntry.objects.get().product, self.product)

    def test_edit_rejects_blank_new_product_name_and_negative_nutrition(self):
        proposal = self._proposal(product_id=None, new_product=NEW_PRODUCT)

        response = self.client.post(
            reverse("assistant:proposal_edit", args=[proposal.pk]),
            {
                "product": "",
                "new_name": "   ",
                "new_base_unit": "g",
                "new_calories": "-1",
                "new_proteins": "8",
                "new_fats": "14",
                "new_carbs": "60",
                "amount": "35",
                "date": "01/11/2026",
                "meal_type": "afternoon_snack",
            },
        )

        self.assertEqual(response.status_code, 200)
        proposal.refresh_from_db()
        self.assertEqual(proposal.new_name, NEW_PRODUCT["name"])
        self.assertEqual(proposal.new_calories, Decimal("420.0"))

    def test_confirm_new_product_creates_product_and_entry(self):
        proposal = self._proposal(product_id=None, new_product=NEW_PRODUCT)

        response = self.client.post(reverse("assistant:proposal_confirm", args=[proposal.pk]))

        self.assertRedirects(response, reverse("assistant:proposals"))
        product = Product.objects.get(name="Печенье овсяное")
        entry = DiaryEntry.objects.get()
        self.assertEqual(product.author, self.member)
        self.assertEqual(entry.product, product)
        self.assertEqual(entry.amount, Decimal("150.0"))

    def test_confirm_new_product_rolls_back_product_when_entry_creation_fails(self):
        proposal = self._proposal(product_id=None, new_product=NEW_PRODUCT)

        with (
            mock.patch.object(DiaryEntry.objects, "create", side_effect=RuntimeError),
            self.assertRaises(RuntimeError),
        ):
            self.client.post(reverse("assistant:proposal_confirm", args=[proposal.pk]))

        self.assertFalse(Product.objects.filter(name="Печенье овсяное").exists())
        self.assertEqual(DiaryEntry.objects.count(), 0)
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, Proposal.Status.PENDING)

    def test_confirm_corrupt_new_product_shows_error_without_data(self):
        proposal = Proposal.objects.create(
            member=self.member,
            date=datetime.date(2026, 10, 31),
            meal_type=DiaryEntry.MealType.LUNCH,
            amount=Decimal("100"),
            new_name="Неполный продукт",
            new_base_unit="g",
        )

        response = self.client.post(
            reverse("assistant:proposal_confirm", args=[proposal.pk]), follow=True
        )

        self.assertContains(response, "не хватает КБЖУ")
        self.assertFalse(Product.objects.filter(name="Неполный продукт").exists())
        self.assertEqual(DiaryEntry.objects.count(), 0)
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, Proposal.Status.PENDING)

    def test_reject_creates_nothing(self):
        proposal = self._proposal()

        response = self.client.post(reverse("assistant:proposal_reject", args=[proposal.pk]))

        self.assertRedirects(response, reverse("assistant:proposals"))
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, Proposal.Status.REJECTED)
        self.assertIsNone(proposal.product)
        self.assertEqual(proposal.product_name, "Молоко 3.2%")
        self.assertEqual(DiaryEntry.objects.count(), 0)
        self.assertContains(self.client.get(reverse("assistant:proposals")), "Молоко 3.2%")

        deleted = self.client.post(
            reverse("core:product_delete", args=[self.product.pk]),
            headers={"HX-Request": "true"},
        )
        self.assertContains(deleted, "Продукт удалён")
        self.assertFalse(Product.objects.filter(pk=self.product.pk).exists())

    def test_confirm_rejected_proposal_shows_error_and_creates_nothing(self):
        proposal = self._proposal()
        self.client.post(reverse("assistant:proposal_reject", args=[proposal.pk]))

        response = self.client.post(
            reverse("assistant:proposal_confirm", args=[proposal.pk]), follow=True
        )

        self.assertContains(response, "уже отклонено")
        self.assertEqual(DiaryEntry.objects.count(), 0)

    def test_product_proposal_card_edit_and_confirmation_without_entry(self):
        proposal = self._product_proposal()

        listed = self.client.get(reverse("assistant:proposals"))
        edit = self.client.get(reverse("assistant:proposal_edit", args=[proposal.pk]))
        self.assertContains(listed, "Печенье овсяное")
        self.assertContains(listed, "новый Продукт")
        self.assertContains(edit, "Правка Предложения Продукта")
        self.assertNotContains(edit, "Правка Предложения Записи")

        edited = self.client.post(
            reverse("assistant:proposal_edit", args=[proposal.pk]),
            {
                "new_name": "Печенье овсяное с изюмом",
                "new_base_unit": "g",
                "new_calories": "400",
                "new_proteins": "8",
                "new_fats": "14",
                "new_carbs": "60",
            },
        )
        self.assertRedirects(edited, reverse("assistant:proposals"))
        proposal.refresh_from_db()
        self.assertEqual(proposal.new_name, "Печенье овсяное с изюмом")
        self.assertIsNone(proposal.date)
        self.assertIsNone(proposal.amount)
        self.assertEqual(proposal.meal_type, "")

        confirmed = self.client.post(
            reverse("assistant:proposal_confirm", args=[proposal.pk]), follow=True
        )
        product = Product.objects.get(name="Печенье овсяное с изюмом")
        proposal.refresh_from_db()
        self.assertEqual(product.author, self.member)
        self.assertEqual(proposal.status, Proposal.Status.CONFIRMED)
        self.assertEqual(proposal.product, product)
        self.assertEqual(DiaryEntry.objects.count(), 0)
        self.assertContains(confirmed, "Продукт добавлен в Каталог")

        self.client.post(reverse("assistant:proposal_confirm", args=[proposal.pk]))
        self.assertEqual(Product.objects.filter(name="Печенье овсяное с изюмом").count(), 1)

    def test_reject_product_proposal_creates_nothing(self):
        proposal = self._product_proposal()

        response = self.client.post(reverse("assistant:proposal_reject", args=[proposal.pk]))

        self.assertRedirects(response, reverse("assistant:proposals"))
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, Proposal.Status.REJECTED)
        self.assertFalse(Product.objects.filter(name="Печенье овсяное").exists())
        self.assertEqual(DiaryEntry.objects.count(), 0)

    def test_confirmed_product_proposal_is_visible_in_catalog_delete_guard(self):
        proposal = self._product_proposal()
        self.client.post(reverse("assistant:proposal_confirm", args=[proposal.pk]))
        product = Product.objects.get(name="Печенье овсяное")

        response = self.client.get(
            reverse("core:product_edit", args=[product.pk]), headers={"HX-Request": "true"}
        )

        self.assertContains(response, "используется в предложении")
        self.assertNotContains(response, "Удалить продукт")

    def test_catalog_product_used_by_proposal_cannot_be_deleted_from_sheet(self):
        self._proposal()

        response = self.client.post(
            reverse("core:product_delete", args=[self.product.pk]),
            headers={"HX-Request": "true"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Product.objects.filter(pk=self.product.pk).exists())
        self.assertContains(response, "Удалить нельзя")

    def test_decision_endpoints_are_post_only_and_member_scoped(self):
        foreign = self._proposal(member=self.other)

        self.assertEqual(
            self.client.get(reverse("assistant:proposal_confirm", args=[foreign.pk])).status_code,
            405,
        )
        self.assertEqual(
            self.client.post(reverse("assistant:proposal_confirm", args=[foreign.pk])).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(reverse("assistant:proposal_reject", args=[foreign.pk])).status_code,
            404,
        )
