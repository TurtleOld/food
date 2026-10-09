import io
import json
import socket
from email.message import Message
from unittest import mock
from urllib.error import HTTPError, URLError

from django.core.cache import cache
from django.urls import reverse

from core.models import Barcode, Product
from core.tests.test_sheet_barcode import EAN13, OTHER_EAN13, SheetBarcodeBase

URLOPEN = "core.off.urlopen"


class FakeResponse:
    def __init__(self, body, status=200):
        self.status = status
        self._body = body if isinstance(body, bytes) else json.dumps(body).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(status, body):
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    return HTTPError("https://off.test", status, "x", Message(), io.BytesIO(raw))


def full_product(**overrides):
    product = {
        "product_name": "Кефир 3,2%",
        "brands": "Простоквашино, Данон",
        "nutrition_data_per": "100ml",
        "product_quantity_unit": "ml",
        "nutriments": {
            "energy_100g": 247,
            "energy-kcal_100g": 59,
            "proteins_100g": 2.9,
            "fat_100g": 3.2,
            "carbohydrates_100g": 4.7,
        },
    }
    product.update(overrides)
    return {"code": OTHER_EAN13, "status": "success", "product": product}


NOT_FOUND = {
    "code": OTHER_EAN13,
    "status": "failure",
    "result": {"id": "product_not_found"},
    "errors": [{"message": {"id": "product_not_found"}}],
}


class OffBase(SheetBarcodeBase):
    def setUp(self):
        super().setUp()
        cache.clear()

    def search_code(self, code=OTHER_EAN13):
        return self.search(code)

    def form(self, code=OTHER_EAN13):
        return self.client.get(self.new_url, {"code": code}, headers={"HX-Request": "true"})


class OffSearchOutcomeTests(OffBase):
    def test_found_product_gets_a_line_with_brand_and_name_leading_to_the_form(self):
        with mock.patch(URLOPEN, return_value=FakeResponse(full_product())):
            response = self.search_code()

        self.assertContains(response, "Простоквашино Кефир 3,2%")
        self.assertContains(response, "Open Food Facts")
        self.assertContains(response, "проверить и добавить")
        self.assertContains(response, f"{self.new_url}?code={OTHER_EAN13}")

    def test_incomplete_nutrition_asks_to_complete(self):
        data = full_product()
        del data["product"]["nutriments"]["fat_100g"]
        with mock.patch(URLOPEN, return_value=FakeResponse(data)):
            response = self.search_code()

        self.assertContains(response, "не все КБЖУ — дополнить")
        self.assertNotContains(response, "проверить и добавить")

    def test_unknown_to_off_says_so_and_still_offers_a_new_product(self):
        with mock.patch(URLOPEN, side_effect=http_error(404, NOT_FOUND)):
            response = self.search_code()

        self.assertContains(response, "нет и в Open Food Facts")
        self.assertContains(response, "+ Новый продукт со штрихкодом")

    def test_503_html_and_timeouts_say_off_did_not_answer(self):
        failures = [
            http_error(503, b"<html>busy</html>"),
            FakeResponse(b"<html>captcha</html>"),
            socket.timeout(),
            URLError("down"),
        ]
        for failure in failures:
            cache.clear()
            kwargs = (
                {"return_value": failure}
                if isinstance(failure, FakeResponse)
                else {"side_effect": failure}
            )
            with self.subTest(failure=repr(failure)), mock.patch(URLOPEN, **kwargs):
                response = self.search_code()

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Open Food Facts не ответил — заполните вручную")
                self.assertContains(response, "+ Новый продукт со штрихкодом")

    def test_404_that_is_not_product_not_found_is_a_failure(self):
        with mock.patch(URLOPEN, side_effect=http_error(404, b"<html>nginx</html>")):
            response = self.search_code()

        self.assertContains(response, "Open Food Facts не ответил")

    def test_code_from_the_catalog_never_goes_to_the_network(self):
        Barcode.objects.create(code=EAN13, product=self.chicken)
        with mock.patch(URLOPEN) as urlopen:
            self.search_code(EAN13)

        urlopen.assert_not_called()

    def test_incomplete_or_invalid_codes_never_go_to_the_network(self):
        with mock.patch(URLOPEN) as urlopen:
            self.search("400638")
            self.search("4006381333932")

        urlopen.assert_not_called()

    def test_bind_mode_never_goes_to_the_network(self):
        with mock.patch(URLOPEN) as urlopen:
            self.search("гре", bind=OTHER_EAN13)

        urlopen.assert_not_called()

    def test_search_shows_a_waiting_line(self):
        response = self.client.get(self.create_url, headers={"HX-Request": "true"})

        self.assertContains(response, "Ищем")
        self.assertContains(response, "в каталоге и Open Food Facts")


class OffRequestTests(OffBase):
    def test_request_is_server_side_limited_and_identified(self):
        with mock.patch(URLOPEN, return_value=FakeResponse(full_product())) as urlopen:
            self.search_code()

        request = urlopen.call_args.args[0]
        self.assertTrue(
            request.full_url.startswith("https://world.openfoodfacts.org/api/v3/product/")
        )
        self.assertIn(OTHER_EAN13, request.full_url)
        self.assertIn("fields=", request.full_url)
        self.assertIn("food-tracker/", request.get_header("User-agent"))
        self.assertLessEqual(urlopen.call_args.kwargs["timeout"], 5)


class OffCacheTests(OffBase):
    def test_found_is_fetched_once_for_search_and_form(self):
        with mock.patch(URLOPEN, return_value=FakeResponse(full_product())) as urlopen:
            self.search_code()
            self.search_code()
            self.form()

        self.assertEqual(urlopen.call_count, 1)

    def test_not_found_is_fetched_once(self):
        with mock.patch(URLOPEN, side_effect=http_error(404, NOT_FOUND)) as urlopen:
            self.search_code()
            self.search_code()

        self.assertEqual(urlopen.call_count, 1)

    def test_failures_are_not_cached(self):
        with mock.patch(URLOPEN, side_effect=URLError("down")) as urlopen:
            self.search_code()
            self.search_code()

        self.assertEqual(urlopen.call_count, 2)


class OffFormTests(OffBase):
    def test_complete_product_prefills_the_form_with_attribution(self):
        with mock.patch(URLOPEN, return_value=FakeResponse(full_product())):
            response = self.form()

        self.assertContains(response, 'value="Простоквашино Кефир 3,2%"')
        self.assertContains(response, 'value="59.0"')
        self.assertContains(response, 'value="2.9"')
        self.assertContains(response, 'value="3.2"')
        self.assertContains(response, 'value="4.7"')
        self.assertContains(response, "Данные: Open Food Facts")
        self.assertContains(response, f"https://world.openfoodfacts.org/product/{OTHER_EAN13}")
        self.assertRegex(
            response.content.decode(), r'value="ml"[^>]*checked|checked[^>]*value="ml"'
        )
        self.assertNotContains(response, "нет в Open Food Facts")

    def test_kilojoules_are_not_taken_as_kilocalories(self):
        data = full_product()
        del data["product"]["nutriments"]["energy-kcal_100g"]
        with mock.patch(URLOPEN, return_value=FakeResponse(data)):
            response = self.form()

        self.assertNotContains(response, 'value="247')
        self.assertContains(response, "нет в Open Food Facts — посмотрите на упаковке")

    def test_per_serving_leaves_the_unit_unchosen(self):
        data = full_product(nutrition_data_per="serving")
        with mock.patch(URLOPEN, return_value=FakeResponse(data)):
            response = self.form()

        self.assertNotRegex(response.content.decode(), r'value="(g|ml)"[^>]*checked')
        self.assertContains(response, "Укажите, на сколько")

    def test_quantity_unit_is_only_a_hint_when_the_basis_is_unclear(self):
        data = full_product(nutrition_data_per="serving", product_quantity_unit="ml")
        with mock.patch(URLOPEN, return_value=FakeResponse(data)):
            response = self.form()

        self.assertContains(response, "в Open Food Facts объём указан в мл")
        self.assertNotRegex(response.content.decode(), r'value="(g|ml)"[^>]*checked')

    def test_quantity_unit_is_not_mentioned_when_the_basis_is_clear(self):
        data = full_product(nutrition_data_per="100ml", product_quantity_unit="ml")
        with mock.patch(URLOPEN, return_value=FakeResponse(data)):
            response = self.form()

        self.assertNotContains(response, "в Open Food Facts объём указан")

    def test_gram_product_selects_grams(self):
        data = full_product(nutrition_data_per="100g")
        with mock.patch(URLOPEN, return_value=FakeResponse(data)):
            response = self.form()

        self.assertRegex(response.content.decode(), r'value="g"[^>]*checked|checked[^>]*value="g"')

    def test_missing_fields_are_flagged_and_saving_with_blanks_is_refused(self):
        data = full_product()
        del data["product"]["nutriments"]["carbohydrates_100g"]
        with mock.patch(URLOPEN, return_value=FakeResponse(data)):
            response = self.form()

        self.assertContains(response, "нет в Open Food Facts — посмотрите на упаковке", count=1)

        posted = self.client.post(
            f"{self.new_url}?code={OTHER_EAN13}",
            {
                "name": "Кефир",
                "base_unit": "ml",
                "calories": "59",
                "proteins": "2.9",
                "fats": "3.2",
                "carbs": "",
                "barcode": OTHER_EAN13,
            },
            headers={"HX-Request": "true"},
        )

        self.assertEqual(posted.status_code, 200)
        self.assertFalse(Product.objects.filter(name="Кефир").exists())

    def test_not_found_and_failure_show_their_own_line(self):
        with mock.patch(URLOPEN, side_effect=http_error(404, NOT_FOUND)):
            missed = self.form()
        cache.clear()
        with mock.patch(URLOPEN, side_effect=URLError("down")):
            failed = self.form()

        self.assertContains(missed, "Код не найден")
        self.assertNotContains(missed, "Данные: Open Food Facts")
        self.assertContains(failed, "Open Food Facts не ответил — заполните вручную")
        self.assertNotContains(failed, "Данные: Open Food Facts")

    def test_catalog_code_in_the_form_url_does_not_go_to_the_network(self):
        Barcode.objects.create(code=EAN13, product=self.chicken)
        with mock.patch(URLOPEN) as urlopen:
            response = self.form(EAN13)

        urlopen.assert_not_called()
        self.assertEqual(response.status_code, 200)

    def test_name_query_without_code_never_goes_to_the_network(self):
        with mock.patch(URLOPEN) as urlopen:
            self.client.get(self.new_url, {"name": "каша"}, headers={"HX-Request": "true"})

        urlopen.assert_not_called()

    def test_saving_a_prefilled_product_stores_no_source(self):
        with mock.patch(URLOPEN, return_value=FakeResponse(full_product())):
            self.form()
        response = self.client.post(
            f"{self.new_url}?code={OTHER_EAN13}",
            {
                "name": "Кефир",
                "base_unit": "ml",
                "calories": "59",
                "proteins": "2.9",
                "fats": "3.2",
                "carbs": "4.7",
                "barcode": OTHER_EAN13,
            },
            headers={"HX-Request": "true"},
        )

        self.assertEqual(Barcode.objects.get(code=OTHER_EAN13).product.name, "Кефир")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            reverse("core:entry_create", args=[self.today.isoformat()]),
            response["Location"].split("?")[0],
        )
