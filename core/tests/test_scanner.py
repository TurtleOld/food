from django.contrib.staticfiles import finders
from django.urls import reverse

from core.tests.test_sheet_add import HTMX, SheetAddBase


class ScannerEntryPointTests(SheetAddBase):
    def test_day_page_scan_button_opens_the_sheet_with_the_camera_requested(self):
        response = self.client.get(reverse("core:day"))

        self.assertContains(response, "?scan=1")

    def test_scan_entry_renders_the_camera_strip_for_the_tap_to_start(self):
        response = self.client.get(self.create_url, {"scan": "1"}, headers=HTMX)

        self.assertContains(response, "scanner({ auto: true })")
        self.assertContains(response, 'id="scanner-strip"')
        self.assertContains(response, "playsinline")
        self.assertContains(response, "muted")
        self.assertContains(response, 'accept="image/*"')
        self.assertContains(response, 'capture="environment"')

    def test_plain_entry_has_the_scan_toggle_but_starts_only_on_tap(self):
        response = self.client.get(self.create_url, headers=HTMX)

        self.assertContains(response, 'aria-label="Сканер штрихкода"')
        self.assertContains(response, "scanner({ auto: false })")

    def test_bind_mode_has_no_scanner(self):
        response = self.client.get(self.create_url, {"bind": "4006381333931"}, headers=HTMX)

        self.assertNotContains(response, 'aria-label="Сканер штрихкода"')

    def test_catalog_hit_row_is_marked_so_a_scan_can_jump_to_amount(self):
        from core.models import Barcode

        Barcode.objects.create(code="4006381333931", product=self.chicken)

        response = self.client.get(self.search_url, {"q": "4006381333931"}, headers=HTMX)

        self.assertContains(response, "data-catalog")


class ScannerProductFormTests(SheetAddBase):
    def test_product_sheet_has_the_scanner_button_next_to_the_digits(self):
        response = self.client.get(reverse("core:product_create"), headers=HTMX)

        self.assertContains(response, 'aria-label="Сканер штрихкода"')


class ScannerAssetsTests(SheetAddBase):
    def test_polyfill_and_wasm_are_vendored_with_pinned_versions(self):
        self.assertIsNotNone(
            finders.find("vendor/barcode-detector/barcode-detector-3.2.2-polyfill.js")
        )
        self.assertIsNotNone(finders.find("vendor/zxing-wasm/zxing_reader-3.1.3.wasm"))

    def test_strip_points_at_the_vendored_assets_not_a_cdn(self):
        response = self.client.get(self.create_url, {"scan": "1"}, headers=HTMX)

        self.assertContains(response, "barcode-detector-3.2.2-polyfill.js")
        self.assertContains(response, "zxing_reader-3.1.3.wasm")
        self.assertNotContains(response, "cdn.")

    def test_service_worker_does_not_precache_the_lazy_scanner_assets(self):
        response = self.client.get(reverse("core:service_worker"))

        self.assertNotIn(b"barcode-detector", response.content)
        self.assertNotIn(b"zxing", response.content)

    def test_pages_do_not_load_the_scanner_assets_eagerly(self):
        response = self.client.get(reverse("core:day"))

        self.assertNotContains(response, "polyfill")
        self.assertNotContains(response, ".wasm")
