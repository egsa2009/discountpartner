"""Tests básicos de deal_finder. Ejecutar: python -m unittest discover -s tests -v"""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import deal_finder as df

# Los módulos imprimen emojis; en consolas Windows (cp1252) fallarían.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


class ExtractPricesTest(unittest.TestCase):
    def test_dos_precios_calcula_descuento(self):
        self.assertEqual(df.extract_prices("Now $50 was $100"), (100.0, 50.0, 50))

    def test_separador_de_miles(self):
        self.assertEqual(df.extract_prices("$1,200 down from $1,600"), (1600.0, 1200.0, 25))

    def test_un_precio_con_porcentaje(self):
        self.assertEqual(df.extract_prices("Only $60 - 40% off"), (100.0, 60.0, 40))

    def test_sin_precios(self):
        self.assertEqual(df.extract_prices("sin datos"), (None, None, None))

    def test_descuento_fuera_de_rango_se_descarta(self):
        self.assertEqual(df.extract_prices("$99 was $100"), (None, None, None))


class AsinAndTagTest(unittest.TestCase):
    def test_extract_asin_dp_y_gp(self):
        self.assertEqual(df._extract_asin("https://www.amazon.com/dp/b0abc12345?x=1"), "B0ABC12345")
        self.assertEqual(df._extract_asin("https://www.amazon.com/gp/product/B0ABC12345"), "B0ABC12345")

    def test_extract_asin_inexistente(self):
        self.assertEqual(df._extract_asin("https://example.com/foo"), "")

    def test_add_affiliate_tag_amazon(self):
        url = df.add_affiliate_tag("https://www.amazon.com/dp/B0ABC12345?tag=otro", "mi-20")
        self.assertEqual(df._tag_from_url(url), "mi-20")

    def test_add_affiliate_tag_ignora_otros_dominios(self):
        url = "https://example.com/dp/B0ABC12345"
        self.assertEqual(df.add_affiliate_tag(url, "mi-20"), url)


class SentAsinsTest(unittest.TestCase):
    def test_ciclo_guardar_cargar_y_expirar(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sent_asins.json"
            with mock.patch.object(df, "SENT_ASINS_PATH", path):
                self.assertEqual(df.load_sent_asins(), {})

                sent = {}
                df.mark_asin_sent(sent, "NUEVO00001")
                viejo = datetime.now(timezone.utc) - timedelta(hours=df.ASIN_COOLDOWN_HOURS + 1)
                sent["VIEJO00001"] = viejo.isoformat()
                df.save_sent_asins(sent)

                cargado = df.load_sent_asins()
                self.assertIn("NUEVO00001", cargado)
                self.assertNotIn("VIEJO00001", cargado)

    def test_archivo_corrupto_devuelve_vacio(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sent_asins.json"
            path.write_text("{no es json", encoding="utf-8")
            with mock.patch.object(df, "SENT_ASINS_PATH", path):
                self.assertEqual(df.load_sent_asins(), {})


if __name__ == "__main__":
    unittest.main()
