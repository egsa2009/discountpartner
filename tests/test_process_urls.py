"""Tests de resolve_amazon_url (sin red). Ejecutar: python -m unittest discover -s tests -v"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import process_urls as pu


def fake_response(final_url):
    return mock.Mock(url=final_url)


class ResolveAmazonUrlTest(unittest.TestCase):
    def test_url_amazon_completa_no_hace_red(self):
        with mock.patch.object(pu.requests, "head") as head:
            url = "https://www.amazon.com/dp/B08HYRFP6W"
            self.assertEqual(pu.resolve_amazon_url(url), url)
            head.assert_not_called()

    def test_link_amazon_nuevo_dominio_se_resuelve(self):
        final = "https://www.amazon.com/dp/B08HYRFP6W/ref=x?tag=t"
        with mock.patch.object(pu.requests, "head", return_value=fake_response(final)):
            self.assertEqual(pu.resolve_amazon_url("https://link.amazon/B06OCK0ZU"), final)

    def test_si_head_falla_usa_get(self):
        final = "https://www.amazon.com/dp/B08HYRFP6W"
        with mock.patch.object(pu.requests, "head", side_effect=Exception("boom")), \
             mock.patch.object(pu.requests, "get", return_value=fake_response(final)):
            self.assertEqual(pu.resolve_amazon_url("https://amzlinks.in/abc"), final)

    def test_sin_resolucion_devuelve_original(self):
        url = "https://link.amazon/xxxx"
        with mock.patch.object(pu.requests, "head", return_value=fake_response("https://otro.com/")), \
             mock.patch.object(pu.requests, "get", return_value=fake_response("https://otro.com/")):
            self.assertEqual(pu.resolve_amazon_url(url), url)


if __name__ == "__main__":
    unittest.main()
