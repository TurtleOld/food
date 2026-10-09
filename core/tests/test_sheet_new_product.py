from django.urls import reverse

from core.models import Product
from core.tests.test_sheet_add import HTMX, SheetAddBase

VALID = {
    "name": "Овсянка",
    "base_unit": "g",
    "calories": "342",
    "proteins": "12.3",
    "fats": "6.1",
    "carbs": "59.5",
}


class SheetNewProductTests(SheetAddBase):
    def setUp(self):
        super().setUp()
        self.new_url = reverse("core:entry_product_new", args=[self.today.isoformat()])

    def test_search_ends_with_a_new_product_row_carrying_the_query(self):
        response = self.client.get(self.search_url, {"q": "овсян", "meal": "dinner"}, headers=HTMX)

        self.assertContains(response, "+ Новый продукт «овсян»")
        self.assertContains(response, "name=%D0%BE%D0%B2%D1%81%D1%8F%D0%BD")

    def test_new_product_row_follows_matches(self):
        response = self.client.get(self.search_url, {"q": "кур"}, headers=HTMX)

        body = response.content.decode()
        self.assertLess(body.index("Курица"), body.index("Новый продукт"))

    def test_new_product_row_is_hidden_for_a_numeric_query_and_empty_query(self):
        for query in ("4600682", ""):
            with self.subTest(query=query):
                response = self.client.get(self.search_url, {"q": query}, headers=HTMX)
                self.assertNotContains(response, "Новый продукт")

    def test_form_opens_in_the_sheet_with_the_query_as_name(self):
        response = self.client.get(self.new_url, {"name": "овсян"}, headers=HTMX)

        self.assertNotContains(response, "<html")
        self.assertContains(response, 'value="овсян"')
        for field in ("base_unit", "calories", "proteins", "fats", "carbs"):
            self.assertContains(response, f'name="{field}"')
        self.assertContains(response, "На 100 г / мл")

    def test_creating_adds_a_catalog_product_authored_by_the_member(self):
        self.client.post(self.new_url, VALID, headers=HTMX)

        product = Product.objects.get(name="Овсянка")
        self.assertEqual(product.author, self.member)
        self.assertEqual(str(product.calories), "342.0")

    def test_creating_leads_to_the_amount_step_for_the_new_product(self):
        response = self.client.post(f"{self.new_url}?meal=dinner", VALID, headers=HTMX, follow=True)

        product = Product.objects.get(name="Овсянка")
        self.assertContains(response, "Добавить в")
        self.assertContains(response, f'value="{product.pk}"')
        self.assertContains(response, "Овсянка")
        self.assertContains(response, "ужин")

    def test_every_nutrition_value_and_the_unit_are_required(self):
        for field in ("base_unit", "calories", "proteins", "fats", "carbs", "name"):
            with self.subTest(field=field):
                data = {**VALID, field: ""}
                response = self.client.post(self.new_url, data, headers=HTMX)

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'name="calories"')
                self.assertContains(response, "help is-danger")
                self.assertFalse(Product.objects.filter(name="Овсянка").exists())

    def test_requires_login_with_hx_redirect(self):
        self.client.logout()

        response = self.client.post(self.new_url, VALID, headers=HTMX)

        self.assertIn("HX-Redirect", response)
