from django.urls import reverse

from core.tests.test_sheet_add import HTMX, SheetAddBase
from core.tests.test_sheet_barcode import EAN13

HOSTILE = '"><script>alert(1)</script>&x=1'


class SheetParamsTests(SheetAddBase):
    def test_hostile_meal_and_hour_do_not_reach_links_or_hx_vals(self):
        response = self.client.get(
            self.search_url, {"q": "кур", "meal": HOSTILE, "hour": HOSTILE}, headers=HTMX
        )

        self.assertNotContains(response, "<script>")
        self.assertNotContains(response, "alert(1)")
        self.assertContains(response, f"product={self.chicken.pk}")

    def test_hostile_meal_and_hour_do_not_reach_the_search_step(self):
        response = self.client.get(
            self.create_url, {"meal": HOSTILE, "hour": HOSTILE, "bind": HOSTILE}, headers=HTMX
        )

        self.assertNotContains(response, "<script>")
        self.assertNotContains(response, "alert(1)")

    def test_out_of_range_hour_is_dropped_but_a_valid_one_is_carried(self):
        dropped = self.client.get(self.search_url, {"q": "кур", "hour": "99"}, headers=HTMX)
        carried = self.client.get(self.search_url, {"q": "кур", "hour": "9"}, headers=HTMX)

        self.assertNotContains(dropped, "hour=99")
        self.assertContains(carried, "hour=9")

    def test_unknown_meal_is_dropped(self):
        response = self.client.get(self.search_url, {"q": "кур", "meal": "brunch"}, headers=HTMX)

        self.assertNotContains(response, "brunch")

    def test_not_this_product_carries_the_hour(self):
        response = self.client.get(
            self.create_url,
            {"product": self.chicken.pk, "code": EAN13, "hour": "9"},
            headers=HTMX,
        )

        self.assertContains(response, "&quot;hour&quot;: &quot;9&quot;")

    def test_search_results_tell_which_query_they_answer(self):
        response = self.client.get(self.search_url, {"q": " кур "}, headers=HTMX)

        self.assertContains(response, 'data-query="кур"')

    def test_new_product_form_ignores_hostile_carried_params(self):
        url = reverse("core:entry_product_new", args=[self.today.isoformat()])

        response = self.client.get(url, {"meal": HOSTILE, "hour": HOSTILE}, headers=HTMX)

        self.assertNotContains(response, "<script>")
