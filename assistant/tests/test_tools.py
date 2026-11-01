import datetime
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase

from assistant.models import Proposal
from assistant.tools import TOOLS, dispatch_tool
from core.models import Barcode, DailyTarget, DiaryEntry, Product
from core.off import OffLookup, Outcome

User = get_user_model()

PROPOSAL = {
    "amount": 150,
    "date": "2026-10-31",
    "meal_type": "lunch",
}


class ToolsSchemaTest(TestCase):
    def test_public_schema_lists_the_available_tools(self):
        functions = {tool["function"]["name"]: tool["function"] for tool in TOOLS}

        self.assertEqual(
            set(functions),
            {
                "search_catalog",
                "lookup_barcode",
                "read_diary",
                "read_profile",
                "propose_product",
                "propose_entry",
            },
        )
        for function in functions.values():
            self.assertTrue(function["description"])
            self.assertEqual(function["parameters"]["type"], "object")
        self.assertEqual(functions["propose_entry"]["parameters"]["required"], ["amount"])
        self.assertEqual(functions["propose_product"]["parameters"]["required"], ["new_product"])


class DispatchTest(TestCase):
    def setUp(self):
        self.member = User.objects.create_user(username="alice", password="s3cret-pass")

    def test_unauthenticated_member_gets_an_error(self):
        result = dispatch_tool(AnonymousUser(), "read_profile", {})

        self.assertIn("error", result)

    def test_unknown_tool_and_bad_arguments_are_reported_as_data(self):
        self.assertIn("error", dispatch_tool(self.member, "delete_everything", {}))
        self.assertIn("error", dispatch_tool(self.member, "read_diary", "{oops"))
        self.assertIn("error", dispatch_tool(self.member, "read_diary", "[]"))
        self.assertIn(
            "error", dispatch_tool(self.member, "read_diary", {"date": "2026-01-01", "x": 1})
        )

    def test_member_argument_missing_required_and_unknown_args_are_rejected(self):
        injected = dispatch_tool(
            self.member, "read_diary", {"member": self.member.pk, "date": "2026-01-01"}
        )
        missing = dispatch_tool(self.member, "read_diary", {})
        missing_product = dispatch_tool(self.member, "propose_product", {})

        for result in (injected, missing, missing_product):
            self.assertIn("error", result)
            self.assertIn("Неверные аргументы", result["error"])

    def test_empty_arguments_are_a_mapping(self):
        self.assertIn("target", dispatch_tool(self.member, "read_profile"))
        self.assertIn("target", dispatch_tool(self.member, "read_profile", ""))


class CatalogToolsTest(TestCase):
    def setUp(self):
        self.member = User.objects.create_user(username="alice", password="s3cret-pass")
        self.product = Product.objects.get(name="Молоко 3.2%")

    def test_search_returns_catalog_products_without_mutation(self):
        before = Product.objects.count()

        result = dispatch_tool(self.member, "search_catalog", {"query": "молоко"})

        self.assertEqual([product["name"] for product in result["products"]], ["Молоко 3.2%"])
        self.assertEqual(result["total"], 1)
        self.assertFalse(result["truncated"])
        self.assertEqual(Product.objects.count(), before)

    def test_search_is_limited_and_truncated_is_reported(self):
        Product.objects.bulk_create(
            Product(
                name=f"Тестовый продукт {index:02d}",
                base_unit="g",
                calories=Decimal("100"),
                proteins=Decimal("1"),
                fats=Decimal("1"),
                carbs=Decimal("1"),
                author=self.member,
            )
            for index in range(21)
        )

        result = dispatch_tool(self.member, "search_catalog", {"query": "тестовый продукт"})

        self.assertEqual(len(result["products"]), 20)
        self.assertEqual(result["total"], 21)
        self.assertTrue(result["truncated"])

    def test_empty_search_query_is_rejected(self):
        self.assertIn("error", dispatch_tool(self.member, "search_catalog", {"query": "  "}))

    def test_barcode_found_in_catalog_does_not_ask_open_food_facts(self):
        Barcode.objects.create(code="4006381333931", product=self.product)

        with mock.patch("assistant.tools.off_lookup") as off_lookup:
            result = dispatch_tool(self.member, "lookup_barcode", {"code": "4006381333931"})

        off_lookup.assert_not_called()
        self.assertEqual(result["product"]["name"], "Молоко 3.2%")
        self.assertIsNone(result["off"])

    def test_unknown_barcode_falls_back_to_open_food_facts(self):
        off = OffLookup(
            outcome=Outcome.FOUND,
            name="Кола",
            base_unit="ml",
            calories=Decimal("42.0"),
            proteins=Decimal("0.0"),
            fats=Decimal("0.0"),
            carbs=Decimal("10.6"),
            url="https://world.openfoodfacts.org/product/4006381333931",
        )

        with mock.patch("assistant.tools.off_lookup", return_value=off):
            result = dispatch_tool(self.member, "lookup_barcode", {"code": "4006381333931"})

        self.assertIsNone(result["product"])
        self.assertEqual(result["off"]["outcome"], "found")
        self.assertEqual(result["off"]["calories"], "42.0")
        self.assertEqual(result["off"]["url"], off.url)

    def test_invalid_barcode_is_rejected(self):
        self.assertIn("error", dispatch_tool(self.member, "lookup_barcode", {"code": "123"}))


class DiaryToolTest(TestCase):
    def setUp(self):
        self.member = User.objects.create_user(username="alice", password="s3cret-pass")
        self.other = User.objects.create_user(username="bob", password="s3cret-pass")
        self.product = Product.objects.get(name="Молоко 3.2%")
        self.entry = DiaryEntry.objects.create(
            member=self.member,
            date=datetime.date(2026, 10, 31),
            meal_type=DiaryEntry.MealType.BREAKFAST,
            product=self.product,
            amount=Decimal("200"),
        )
        DiaryEntry.objects.create(
            member=self.other,
            date=datetime.date(2026, 10, 31),
            meal_type=DiaryEntry.MealType.LUNCH,
            product=self.product,
            amount=Decimal("500"),
        )

    def test_day_read_is_scoped_to_the_member(self):
        result = dispatch_tool(self.member, "read_diary", {"date": "2026-10-31"})

        day = result["days"][0]
        self.assertEqual(len(day["entries"]), 1)
        self.assertEqual(day["entries"][0]["product"], "Молоко 3.2%")
        self.assertEqual(Decimal(day["calories"]), Decimal("120.0"))
        self.assertEqual(day["entries"][0]["amount"], "200.0")

    def test_range_read_returns_each_day_and_rejects_bad_ranges(self):
        DiaryEntry.objects.create(
            member=self.member,
            date=datetime.date(2026, 11, 1),
            meal_type=DiaryEntry.MealType.DINNER,
            product=self.product,
            amount=Decimal("100"),
        )

        result = dispatch_tool(
            self.member, "read_diary", {"date": "2026-10-31", "date_to": "2026-11-01"}
        )

        self.assertEqual([day["date"] for day in result["days"]], ["2026-10-31", "2026-11-01"])
        self.assertIn("error", dispatch_tool(self.member, "read_diary", {"date": "31.10.2026"}))
        self.assertIn(
            "error",
            dispatch_tool(
                self.member, "read_diary", {"date": "2026-11-01", "date_to": "2026-10-31"}
            ),
        )
        self.assertIn(
            "error",
            dispatch_tool(
                self.member, "read_diary", {"date": "2026-01-01", "date_to": "2026-02-15"}
            ),
        )
        self.assertIn(
            "error",
            dispatch_tool(
                self.member, "read_diary", {"date": "2026-01-01", "date_to": "not-a-date"}
            ),
        )


class ProfileToolTest(TestCase):
    def setUp(self):
        self.member = User.objects.create_user(username="alice", password="s3cret-pass")

    def test_without_profile_module_target_is_still_read(self):
        DailyTarget.objects.create(
            member=self.member,
            calories=Decimal("2000"),
            proteins=Decimal("120"),
            fats=Decimal("70"),
            carbs=Decimal("220"),
        )

        result = dispatch_tool(self.member, "read_profile", {})

        self.assertIsNone(result["profile"])
        self.assertIsNone(result["suggestion"])
        self.assertEqual(result["target"]["calories"], "2000.0")

    def test_profile_and_suggestion_are_serialized_when_available(self):
        profile = SimpleNamespace(
            height_cm=180,
            birth_date=datetime.date(1990, 1, 2),
            sex="male",
            activity="moderate",
            is_complete=True,
        )
        suggestion = SimpleNamespace(
            maintenance_kcal=Decimal("2400"),
            adjustment_kcal=Decimal("0"),
            calories=Decimal("2400"),
            proteins=Decimal("120"),
            fats=Decimal("70"),
            carbs=Decimal("300"),
        )
        module = SimpleNamespace(
            profile_of=lambda member: profile,
            target_suggestion=lambda member: suggestion,
        )

        with mock.patch("assistant.tools._profile_module", return_value=module):
            result = dispatch_tool(self.member, "read_profile", {})

        self.assertEqual(
            result["profile"],
            {
                "height_cm": 180,
                "birth_date": "1990-01-02",
                "sex": "male",
                "activity": "moderate",
                "is_complete": True,
            },
        )
        self.assertEqual(result["suggestion"]["calories"], "2400")

    def test_incomplete_optional_profile_interface_returns_nulls(self):
        profile = SimpleNamespace(
            height_cm=None,
            birth_date=None,
            sex="",
            activity="",
            is_complete=False,
        )
        module = SimpleNamespace(profile_of=lambda member: profile)

        with mock.patch("assistant.tools._profile_module", return_value=module):
            result = dispatch_tool(self.member, "read_profile", {})

        self.assertIsNone(result["profile"]["birth_date"])
        self.assertIsNone(result["suggestion"])

        with mock.patch(
            "assistant.tools._profile_module",
            return_value=SimpleNamespace(
                profile_of=lambda member: None, target_suggestion=lambda member: None
            ),
        ):
            result = dispatch_tool(self.member, "read_profile", {})

        self.assertIsNone(result["profile"])
        self.assertIsNone(result["suggestion"])

        with mock.patch(
            "assistant.tools._profile_module",
            return_value=SimpleNamespace(profile_of=lambda m: None),
        ):
            self.assertIsNone(dispatch_tool(self.member, "read_profile", {})["profile"])

        with mock.patch("assistant.tools._profile_module", return_value=SimpleNamespace()):
            self.assertIsNone(dispatch_tool(self.member, "read_profile", {})["profile"])

    def test_import_error_inside_another_module_is_not_hidden(self):
        error = ModuleNotFoundError("No module named 'numpy'", name="numpy")

        with (
            mock.patch("assistant.tools.importlib.import_module", side_effect=error),
            self.assertRaises(ModuleNotFoundError),
        ):
            dispatch_tool(self.member, "read_profile", {})


class ProposeEntryToolTest(TestCase):
    def setUp(self):
        self.member = User.objects.create_user(username="alice", password="s3cret-pass")
        self.product = Product.objects.get(name="Молоко 3.2%")

    def test_missing_date_and_meal_are_asked_before_a_proposal_exists(self):
        for args, missing in (
            ({"amount": 100, "meal_type": "lunch"}, ["date"]),
            ({"amount": 100, "date": "2026-10-31"}, ["meal_type"]),
            ({"amount": 100}, ["date", "meal_type"]),
        ):
            with self.subTest(missing=missing):
                result = dispatch_tool(
                    self.member, "propose_entry", {"product_id": self.product.pk, **args}
                )

                self.assertTrue(result["needs_clarification"])
                self.assertEqual(result["missing"], missing)
                self.assertTrue(result["question"])
                self.assertEqual(Proposal.objects.count(), 0)

    def test_missing_amount_is_an_error(self):
        result = dispatch_tool(
            self.member,
            "propose_entry",
            {"product_id": self.product.pk, "date": "2026-10-31", "meal_type": "lunch"},
        )

        self.assertIn("error", result)
        self.assertEqual(Proposal.objects.count(), 0)

    def test_existing_product_proposal_from_json_arguments(self):
        result = dispatch_tool(
            self.member,
            "propose_entry",
            json.dumps({"product_id": self.product.pk, **PROPOSAL}),
        )

        self.assertEqual(result["proposal"]["status"], Proposal.Status.PENDING)
        self.assertEqual(result["proposal"]["product"]["name"], "Молоко 3.2%")
        self.assertEqual(result["similar"], [])
        proposal = Proposal.objects.get()
        self.assertEqual(proposal.member, self.member)
        self.assertEqual(DiaryEntry.objects.count(), 0)

    def test_new_product_returns_similar_catalog_products_and_does_not_merge(self):
        before = list(Product.objects.values_list("pk", "name", "calories"))

        result = dispatch_tool(
            self.member,
            "propose_entry",
            {
                "amount": 30,
                "date": "2026-10-31",
                "meal_type": "breakfast",
                "new_product": {
                    "name": "Молоко 3.2% обезжиренное",
                    "base_unit": "ml",
                    "calories": 35,
                    "proteins": 3,
                    "fats": 1,
                    "carbs": 4.7,
                },
            },
        )

        self.assertEqual(result["proposal"]["new_product"]["name"], "Молоко 3.2% обезжиренное")
        self.assertEqual([item["name"] for item in result["similar"]], ["Молоко 3.2%"])
        self.assertEqual(list(Product.objects.values_list("pk", "name", "calories")), before)

    def test_invalid_product_data_is_an_error_without_a_proposal(self):
        result = dispatch_tool(
            self.member,
            "propose_entry",
            {
                "amount": 30,
                "date": "2026-10-31",
                "meal_type": "breakfast",
                "new_product": {"name": "Без КБЖУ"},
            },
        )

        self.assertIn("error", result)
        self.assertEqual(Proposal.objects.count(), 0)

    def test_bad_amount_date_meal_product_and_new_product_are_errors(self):
        good_new = {
            "name": "Печенье",
            "base_unit": "g",
            "calories": 420,
            "proteins": 7.5,
            "fats": 16,
            "carbs": 63,
        }
        cases = [
            {"product_id": 999999, **PROPOSAL},
            {**PROPOSAL, "product_id": self.product.pk, "amount": "0"},
            {**PROPOSAL, "product_id": self.product.pk, "amount": "abc"},
            {**PROPOSAL, "product_id": self.product.pk, "date": "31.10.2026"},
            {**PROPOSAL, "product_id": self.product.pk, "meal_type": "supper"},
            {**PROPOSAL, "product_id": self.product.pk, "new_product": good_new},
            {**PROPOSAL},
            {**PROPOSAL, "new_product": {**good_new, "base_unit": "kg"}},
            {**PROPOSAL, "new_product": {**good_new, "calories": -1}},
        ]
        for args in cases:
            with self.subTest(args=args):
                result = dispatch_tool(self.member, "propose_entry", args)

                self.assertIn("error", result)
                self.assertEqual(Proposal.objects.count(), 0)


class ProposeProductToolTest(TestCase):
    def setUp(self):
        self.member = User.objects.create_user(username="alice", password="s3cret-pass")

    def test_product_proposal_has_no_diary_entry_or_date(self):
        products_before = Product.objects.count()

        result = dispatch_tool(
            self.member,
            "propose_product",
            {
                "new_product": {
                    "name": "Молоко 3.2% обезжиренное",
                    "base_unit": "ml",
                    "calories": 35,
                    "proteins": 3,
                    "fats": 1,
                    "carbs": 4.7,
                }
            },
        )

        self.assertEqual(result["proposal"]["kind"], Proposal.Kind.PRODUCT)
        self.assertNotIn("date", result["proposal"])
        self.assertEqual([item["name"] for item in result["similar"]], ["Молоко 3.2%"])
        proposal = Proposal.objects.get()
        self.assertEqual(proposal.kind, Proposal.Kind.PRODUCT)
        self.assertIsNone(proposal.date)
        self.assertIsNone(proposal.amount)
        self.assertEqual(proposal.meal_type, "")
        self.assertEqual(DiaryEntry.objects.count(), 0)
        self.assertEqual(Product.objects.count(), products_before)

    def test_product_proposal_requires_new_product_data(self):
        result = dispatch_tool(self.member, "propose_product", {"new_product": {"name": "X"}})

        self.assertIn("error", result)
        self.assertEqual(Proposal.objects.count(), 0)

    def test_similar_products_are_limited_to_five(self):
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

        result = dispatch_tool(
            self.member,
            "propose_product",
            {
                "new_product": {
                    "name": "Похожий продукт",
                    "base_unit": "g",
                    "calories": 100,
                    "proteins": 1,
                    "fats": 1,
                    "carbs": 1,
                }
            },
        )

        self.assertEqual(len(result["similar"]), 5)

    def test_huge_numbers_are_domain_errors_not_crashes(self):
        product = Product.objects.get(name="Молоко 3.2%")
        huge_entry = dispatch_tool(
            self.member,
            "propose_entry",
            {"product_id": product.pk, **PROPOSAL, "amount": "1e1000"},
        )
        huge_nutrition = dispatch_tool(
            self.member,
            "propose_product",
            {
                "new_product": {
                    "name": "Огромный продукт",
                    "base_unit": "g",
                    "calories": "1e1000",
                    "proteins": 1,
                    "fats": 1,
                    "carbs": 1,
                }
            },
        )

        self.assertIn("error", huge_entry)
        self.assertIn("error", huge_nutrition)
        self.assertEqual(Proposal.objects.count(), 0)
