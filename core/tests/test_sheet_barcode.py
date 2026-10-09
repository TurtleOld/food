import io
from email.message import Message
from unittest import mock
from urllib.error import HTTPError

from django.core.cache import cache
from django.urls import reverse

from core.models import Barcode, Product
from core.tests.test_sheet_add import HTMX, SheetAddBase

EAN13 = "4006381333931"
OTHER_EAN13 = "4607000000014"


def _off_not_found(*args, **kwargs):
    body = io.BytesIO(b'{"result": {"id": "product_not_found"}}')
    raise HTTPError("https://off.test", 404, "Not Found", Message(), body)


class SheetBarcodeBase(SheetAddBase):
    def setUp(self):
        super().setUp()
        # Тесты не ходят в сеть: по умолчанию Open Food Facts «не знает» ни одного кода.
        cache.clear()
        patcher = mock.patch("core.off.urlopen", side_effect=_off_not_found)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.new_url = reverse("core:entry_product_new", args=[self.today.isoformat()])
        self.bind_url = reverse("core:entry_barcode_bind", args=[self.today.isoformat()])
        self.unbind_url = reverse("core:entry_barcode_unbind", args=[self.today.isoformat()])

    def search(self, q, **extra):
        return self.client.get(self.search_url, {"q": q, **extra}, headers=HTMX)


class SheetBarcodeSearchTests(SheetBarcodeBase):
    def setUp(self):
        super().setUp()
        Barcode.objects.create(code=EAN13, product=self.chicken)

    def test_placeholder_invites_digits(self):
        response = self.client.get(self.create_url, headers=HTMX)

        self.assertContains(response, "Название или цифры штрихкода")

    def test_catalog_code_gives_a_barcode_group_with_the_product_leading_to_amount(self):
        response = self.search(EAN13)

        self.assertContains(response, "Штрихкод")
        self.assertContains(response, "Курица")
        self.assertContains(response, "▦ в каталоге")
        self.assertContains(response, f"?product={self.chicken.pk}")
        self.assertNotContains(response, "Новый продукт")

    def test_spaced_upc_a_finds_the_same_product(self):
        Barcode.objects.create(code="0036000291452", product=self.buckwheat)

        response = self.search("0360 0029 1452")

        self.assertContains(response, "Гречка")
        self.assertContains(response, "▦ в каталоге")

    def test_full_code_outside_the_catalog_offers_a_new_product_with_the_code(self):
        response = self.search(OTHER_EAN13)

        self.assertContains(response, "+ Новый продукт со штрихкодом")
        self.assertContains(response, f"code={OTHER_EAN13}")
        self.assertContains(response, "Уже есть в каталоге? Привязать код к продукту")
        self.assertNotContains(response, "Новый продукт «")

    def test_incomplete_digits_search_the_prefix_with_an_explanation(self):
        response = self.search("400638")

        self.assertContains(response, "Штрихкод")
        self.assertContains(response, "Курица")
        self.assertContains(response, "неполный")
        self.assertNotContains(response, "Новый продукт")

    def test_wrong_check_digit_is_treated_as_incomplete(self):
        response = self.search("4006381333932")

        self.assertContains(response, "неверная контрольная цифра")
        self.assertNotContains(response, "Новый продукт")
        self.assertEqual(Barcode.objects.count(), 1)

    def test_short_digits_stay_an_ordinary_search(self):
        response = self.search("40063")

        self.assertNotContains(response, "Штрихкод")
        self.assertContains(response, "Ничего не найдено")

    def test_bind_mode_lists_products_that_bind_the_code_on_tap(self):
        response = self.search("гре", bind=OTHER_EAN13)

        self.assertContains(response, self.bind_url)
        self.assertContains(response, OTHER_EAN13)
        self.assertNotContains(response, "Новый продукт")

    def test_catalog_hit_reaches_the_amount_step_with_a_not_this_product_link(self):
        response = self.client.get(
            self.create_url, {"product": self.chicken.pk, "code": EAN13}, headers=HTMX
        )

        self.assertContains(response, "Не тот продукт?")
        self.assertContains(response, self.unbind_url)

    def test_amount_step_without_a_code_has_no_not_this_product_link(self):
        response = self.client.get(self.create_url, {"product": self.chicken.pk}, headers=HTMX)

        self.assertNotContains(response, "Не тот продукт?")

    def test_bind_search_step_opens_with_the_code_in_the_header(self):
        response = self.client.get(self.create_url, {"bind": OTHER_EAN13}, headers=HTMX)

        self.assertContains(response, OTHER_EAN13)
        self.assertContains(response, f"&quot;bind&quot;: &quot;{OTHER_EAN13}&quot;")


class SheetBarcodeNewProductTests(SheetBarcodeBase):
    def test_form_shows_the_code_chip_and_the_not_found_line(self):
        response = self.client.get(self.new_url, {"code": OTHER_EAN13}, headers=HTMX)

        self.assertContains(response, f"▦ {OTHER_EAN13}")
        self.assertContains(response, "Код не найден — заполните по упаковке")
        self.assertContains(response, "Уже есть в каталоге?")

    def test_invalid_code_in_the_url_is_ignored(self):
        response = self.client.get(self.new_url, {"code": "123"}, headers=HTMX)

        self.assertNotContains(response, "Код не найден")

    def test_creating_binds_the_code_and_leads_to_the_amount_step(self):
        data = {
            "name": "Кефир",
            "base_unit": "ml",
            "calories": "59",
            "proteins": "2.9",
            "fats": "3.2",
            "carbs": "4.7",
            "barcode": OTHER_EAN13,
        }
        response = self.client.post(
            f"{self.new_url}?code={OTHER_EAN13}", data, headers=HTMX, follow=True
        )

        product = Product.objects.get(name="Кефир")
        self.assertEqual(Barcode.objects.get(code=OTHER_EAN13).product, product)
        self.assertContains(response, "Добавить в")


class SheetBarcodeBindTests(SheetBarcodeBase):
    def test_choosing_a_product_binds_the_code_and_leads_to_amount(self):
        response = self.client.post(
            self.bind_url,
            {"product": self.chicken.pk, "code": OTHER_EAN13, "meal": "lunch"},
            headers=HTMX,
            follow=True,
        )

        self.assertEqual(Barcode.objects.get(code=OTHER_EAN13).product, self.chicken)
        self.assertContains(response, "Добавить в")
        self.assertContains(response, "Курица")

    def test_invalid_code_binds_nothing(self):
        response = self.client.post(
            self.bind_url, {"product": self.chicken.pk, "code": "123"}, headers=HTMX
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(Barcode.objects.count(), 0)

    def test_code_already_owned_is_not_stolen(self):
        Barcode.objects.create(code=OTHER_EAN13, product=self.buckwheat)

        self.client.post(
            self.bind_url, {"product": self.chicken.pk, "code": OTHER_EAN13}, headers=HTMX
        )

        self.assertEqual(Barcode.objects.get(code=OTHER_EAN13).product, self.buckwheat)

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(self.bind_url).status_code, 405)


class SheetBarcodeUnbindTests(SheetBarcodeBase):
    def test_not_this_product_unbinds_and_returns_to_the_new_product_form(self):
        Barcode.objects.create(code=EAN13, product=self.chicken)

        response = self.client.post(
            self.unbind_url, {"code": EAN13, "meal": "lunch"}, headers=HTMX, follow=True
        )

        self.assertFalse(Barcode.objects.filter(code=EAN13).exists())
        self.assertContains(response, "Новый продукт")
        self.assertContains(response, f"▦ {EAN13}")
        self.assertContains(response, "Код не найден — заполните по упаковке")
        self.assertTrue(Product.objects.filter(pk=self.chicken.pk).exists())

    def test_requires_login(self):
        self.client.logout()

        response = self.client.post(self.unbind_url, {"code": EAN13}, headers=HTMX)

        self.assertIn("HX-Redirect", response)
