from django.db import IntegrityError, transaction
from django.urls import reverse

from core.models import Barcode, Product
from core.tests.test_catalog_sheet import VALID, CatalogBase
from core.tests.test_sheet_add import HTMX
from core.tests.test_undo import token_of

EAN13 = "4006381333931"
EAN8 = "96385074"
UPC_A = "036000291452"
UPC_A_AS_EAN13 = "0036000291452"


class BarcodeBase(CatalogBase):
    def save_with(self, product, code, **extra):
        return self.client.post(
            self.edit_url(product),
            {**VALID, "name": product.name, "barcode": code, **extra},
            headers=HTMX,
        )

    def codes(self, product):
        return sorted(product.barcodes.values_list("code", flat=True))


class BarcodeModelTests(BarcodeBase):
    def test_code_is_unique_in_the_catalog(self):
        Barcode.objects.create(code=EAN13, product=self.chicken)

        with self.assertRaises(IntegrityError), transaction.atomic():
            Barcode.objects.create(code=EAN13, product=self.buckwheat)

    def test_product_may_have_several_codes_and_deleting_it_removes_them(self):
        Barcode.objects.create(code=EAN13, product=self.chicken)
        Barcode.objects.create(code=EAN8, product=self.chicken)

        self.chicken.delete()

        self.assertEqual(Barcode.objects.count(), 0)


class BarcodeAddTests(BarcodeBase):
    def test_valid_code_is_bound_on_save(self):
        response = self.save_with(self.chicken, EAN13)

        self.assertIn("sheet:close", response["HX-Trigger"])
        self.assertEqual(self.codes(self.chicken), [EAN13])

    def test_upc_a_and_its_ean13_are_the_same_record(self):
        self.save_with(self.chicken, UPC_A)
        self.assertEqual(self.codes(self.chicken), [UPC_A_AS_EAN13])

        response = self.save_with(self.buckwheat, UPC_A_AS_EAN13)

        self.assertContains(response, "Код уже у «Курица»")
        self.assertEqual(self.codes(self.buckwheat), [])

    def test_bad_check_digit_is_rejected_with_an_error(self):
        response = self.save_with(self.chicken, "4006381333932")

        self.assertContains(response, "help is-danger")
        self.assertContains(response, "контрольная цифра")
        self.assertEqual(self.codes(self.chicken), [])

    def test_code_of_another_product_is_not_taken_silently(self):
        Barcode.objects.create(code=EAN13, product=self.buckwheat)

        response = self.save_with(self.chicken, EAN13)

        self.assertContains(response, "Код уже у «Гречка варёная»")
        self.assertContains(response, "Перенести сюда")
        self.assertEqual(self.codes(self.buckwheat), [EAN13])
        self.assertEqual(self.codes(self.chicken), [])

    def test_explicit_transfer_moves_the_code_on_save(self):
        Barcode.objects.create(code=EAN13, product=self.buckwheat)

        self.save_with(self.chicken, EAN13, transfer="1")

        self.assertEqual(self.codes(self.buckwheat), [])
        self.assertEqual(self.codes(self.chicken), [EAN13])

    def test_new_product_can_be_created_with_a_code(self):
        self.client.post(self.create_url, {**VALID, "barcode": EAN8}, headers=HTMX)

        self.assertEqual(self.codes(Product.objects.get(name="Овсянка")), [EAN8])

    def test_rebinding_own_code_is_harmless(self):
        Barcode.objects.create(code=EAN13, product=self.chicken)

        self.save_with(self.chicken, EAN13)

        self.assertEqual(self.codes(self.chicken), [EAN13])


class BarcodeSheetTests(BarcodeBase):
    def test_sheet_shows_chips_for_bound_codes(self):
        Barcode.objects.create(code=EAN13, product=self.chicken)

        response = self.client.get(self.edit_url(self.chicken), headers=HTMX)

        self.assertContains(response, f"▦ {EAN13}")
        self.assertContains(response, "Цифры штрихкода")


class BarcodeUnbindTests(BarcodeBase):
    def unbind_url(self, barcode):
        return reverse("core:barcode_unbind", args=[barcode.pk])

    def test_unbind_removes_the_code_and_offers_undo(self):
        barcode = Barcode.objects.create(code=EAN13, product=self.chicken)

        response = self.client.post(self.unbind_url(barcode), headers=HTMX)

        self.assertEqual(self.codes(self.chicken), [])
        self.assertContains(response, "Вернуть")
        self.assertContains(response, 'id="barcodes"')
        self.assertNotContains(response, f"▦ {EAN13}")

    def test_undo_binds_the_code_back(self):
        barcode = Barcode.objects.create(code=EAN13, product=self.chicken)
        token = token_of(self.client.post(self.unbind_url(barcode), headers=HTMX))

        undone = self.client.post(reverse("core:undo"), {"token": token}, headers=HTMX)

        self.assertEqual(self.codes(self.chicken), [EAN13])
        self.assertContains(undone, "Возвращено")

    def test_undo_redraws_the_chips_of_an_open_product_sheet(self):
        barcode = Barcode.objects.create(code=EAN13, product=self.chicken)
        token = token_of(self.client.post(self.unbind_url(barcode), headers=HTMX))

        undone = self.client.post(reverse("core:undo"), {"token": token}, headers=HTMX)

        self.assertContains(
            undone, f"hx-swap-oob=\"outerHTML:#barcodes[data-product='{self.chicken.pk}']\""
        )
        self.assertContains(undone, f"▦ {EAN13}")

    def test_undo_is_refused_when_the_code_was_taken_meanwhile(self):
        barcode = Barcode.objects.create(code=EAN13, product=self.chicken)
        token = token_of(self.client.post(self.unbind_url(barcode), headers=HTMX))
        Barcode.objects.create(code=EAN13, product=self.buckwheat)

        undone = self.client.post(reverse("core:undo"), {"token": token}, headers=HTMX)

        self.assertContains(undone, "Уже нельзя вернуть")
        self.assertEqual(self.codes(self.chicken), [])

    def test_undo_of_product_deletion_brings_its_codes_back(self):
        Barcode.objects.create(code=EAN13, product=self.chicken)
        token = token_of(self.client.post(self.delete_url(self.chicken), headers=HTMX))

        self.client.post(reverse("core:undo"), {"token": token}, headers=HTMX)

        self.assertEqual(self.codes(Product.objects.get(name="Курица")), [EAN13])


class BarcodeSearchTests(BarcodeBase):
    def setUp(self):
        super().setUp()
        Barcode.objects.create(code=EAN13, product=self.chicken)
        Barcode.objects.create(code=UPC_A_AS_EAN13, product=self.buckwheat)

    def search(self, q):
        return self.client.get(self.list_url, {"q": q}, headers=HTMX)

    def test_digits_match_a_code_prefix(self):
        response = self.search("40063")

        self.assertContains(response, "Курица")
        self.assertNotContains(response, "Гречка")

    def test_upc_a_prefix_finds_the_ean13_record(self):
        response = self.search("03600")

        self.assertContains(response, "Гречка")
        self.assertNotContains(response, "Курица")

    def test_rows_with_codes_have_a_marker_and_others_do_not(self):
        response = self.client.get(self.list_url, headers=HTMX)
        self.assertContains(response, "▦", count=2)

    def test_digit_query_does_not_offer_a_new_product_by_name(self):
        self.assertNotContains(self.search("99999"), "+ Новый продукт")
