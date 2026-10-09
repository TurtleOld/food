from django.test import SimpleTestCase

from core.barcodes import normalize_barcode


class NormalizeBarcodeTests(SimpleTestCase):
    def test_table(self):
        cases = [
            ("4006381333931", "4006381333931"),  # EAN-13
            ("96385074", "96385074"),  # EAN-8
            ("036000291452", "0036000291452"),  # UPC-A -> EAN-13
            ("0036000291452", "0036000291452"),  # the same code as EAN-13
            (" 4006 3813 33931 ", "4006381333931"),  # spaces from copy-paste
            ("4006381333932", None),  # wrong check digit
            ("96385075", None),
            ("036000291453", None),
            ("40063813339", None),  # 11 digits
            ("400638133393", None),  # 12 digits, wrong sum
            ("40063813339A1", None),  # letters
            ("", None),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(normalize_barcode(raw), expected)
