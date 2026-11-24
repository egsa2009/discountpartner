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


class ReintentosScraperTest(unittest.TestCase):
    def setUp(self):
        self.sleep = mock.patch.object(pu.time, "sleep").start()
        mock.patch.object(pu, "SCRAPER_KEY", "clave-falsa").start()
        self.addCleanup(mock.patch.stopall)

    def test_timeout_con_render_reintenta_sin_render(self):
        ok = mock.Mock(status_code=200)
        session = mock.Mock()
        session.get.side_effect = [pu.requests.exceptions.ReadTimeout("lento"), ok]
        resp, err = pu._get_con_reintentos("https://www.amazon.com/dp/B08HYRFP6W", session)
        self.assertIs(resp, ok)
        self.assertIsNone(err)
        self.assertIn("render=true", session.get.call_args_list[0].args[0])
        self.assertIn("render=false", session.get.call_args_list[1].args[0])

    def test_falla_todo_devuelve_error(self):
        session = mock.Mock()
        session.get.return_value = mock.Mock(status_code=500)
        resp, err = pu._get_con_reintentos("https://www.amazon.com/dp/B08HYRFP6W", session)
        self.assertIsNone(resp)
        self.assertIn("HTTP 500", err)
        self.assertEqual(session.get.call_count, 2)

    def test_403_cambia_a_la_otra_cuenta_y_descarta_la_muerta(self):
        mock.patch.object(pu, "SCRAPER_KEYS", ["k-sin-creditos", "k-buena"]).start()
        mock.patch.object(pu, "_KEYS_MUERTAS", set()).start()
        ok = mock.Mock(status_code=200)
        sin_creditos = mock.Mock(status_code=403, text="Account out of credits")
        session = mock.Mock()
        session.get.side_effect = [sin_creditos, ok]
        resp, err = pu._get_con_reintentos("https://www.amazon.com/dp/B08HYRFP6W", session)
        self.assertIs(resp, ok)
        self.assertIn("api_key=k-sin-creditos", session.get.call_args_list[0].args[0])
        self.assertIn("api_key=k-buena", session.get.call_args_list[1].args[0])
        self.assertEqual(pu._KEYS_MUERTAS, {"k-sin-creditos"})

    def test_clave_muerta_no_se_vuelve_a_usar(self):
        mock.patch.object(pu, "SCRAPER_KEYS", ["k1", "k2"]).start()
        mock.patch.object(pu, "_KEYS_MUERTAS", {"k1"}).start()
        ok = mock.Mock(status_code=200)
        session = mock.Mock()
        session.get.return_value = ok
        pu._get_con_reintentos("https://www.amazon.com/dp/B08HYRFP6W", session)
        self.assertEqual(session.get.call_count, 1)
        self.assertIn("api_key=k2", session.get.call_args.args[0])


class CreditosScraperTest(unittest.TestCase):
    def setUp(self):
        for nombre, valor in [("_KEY_ACTUAL", "ka"), ("_KEY_NUEVA", "kn"), ("_KEY_3", "k3"),
                              ("_CUENTA", "auto"), ("SCRAPER_KEY", "ka"),
                              ("SCRAPER_KEYS", ["ka", "kn", "k3"])]:
            mock.patch.object(pu, nombre, valor).start()
        mock.patch.object(pu, "_POR_CUENTA", {"actual": "ka", "nueva": "kn", "3": "k3"}).start()
        mock.patch.object(pu, "_KEYS_MUERTAS", set()).start()
        self.addCleanup(mock.patch.stopall)

    def _con_creditos(self, valores):
        mock.patch.object(pu, "creditos_restantes", side_effect=lambda k: valores[k]).start()

    def test_auto_usa_la_cuenta_con_mas_creditos(self):
        self._con_creditos({"ka": 100, "kn": 0, "k3": 5000})
        pu.ordenar_claves_por_creditos()
        self.assertEqual(pu.SCRAPER_KEYS, ["k3", "ka", "kn"])
        self.assertEqual(pu.SCRAPER_KEY, "k3")
        self.assertEqual(pu._KEYS_MUERTAS, {"kn"})

    def test_cuenta_forzada_va_primero_si_tiene_creditos(self):
        mock.patch.object(pu, "_CUENTA", "actual").start()
        self._con_creditos({"ka": 50, "kn": 900, "k3": 5000})
        pu.ordenar_claves_por_creditos()
        self.assertEqual(pu.SCRAPER_KEY, "ka")
        self.assertEqual(pu.SCRAPER_KEYS[0], "ka")

    def test_cuenta_forzada_sin_creditos_se_salta(self):
        mock.patch.object(pu, "_CUENTA", "actual").start()
        self._con_creditos({"ka": 0, "kn": 900, "k3": 5000})
        pu.ordenar_claves_por_creditos()
        self.assertEqual(pu.SCRAPER_KEY, "k3")

    def test_si_no_se_puede_consultar_no_se_descarta(self):
        self._con_creditos({"ka": None, "kn": None, "k3": 10})
        pu.ordenar_claves_por_creditos()
        self.assertEqual(pu.SCRAPER_KEY, "k3")
        self.assertEqual(pu._KEYS_MUERTAS, set())

    def test_creditos_restantes_lee_creditsleft(self):
        resp = mock.Mock(status_code=200)
        resp.json.return_value = {"creditsLeft": 4321, "requestLimit": 5000, "requestCount": 679}
        with mock.patch.object(pu.requests, "get", return_value=resp):
            self.assertEqual(pu.creditos_restantes("x"), 4321)

    def test_creditos_restantes_403_es_cero(self):
        with mock.patch.object(pu.requests, "get", return_value=mock.Mock(status_code=403)):
            self.assertEqual(pu.creditos_restantes("x"), 0)


if __name__ == "__main__":
    unittest.main()
