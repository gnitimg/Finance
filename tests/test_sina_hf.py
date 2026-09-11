import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.models import FinanceError
from scripts.providers.sina import quote_hf


PAYLOAD = (
    'var hq_str_hf_GC="4407.640,,4403.400,4403.600,4444.900,4333.000,01:51:17,'
    '4407.300,4359.400,0,2,1,2026-09-12,纽约黄金,0";'
).encode("gb18030")

EMPTY = 'var hq_str_hf_PL="";'.encode("gb18030")


class SinaInternationalTests(unittest.TestCase):
    @patch("scripts.providers.sina.request_bytes", return_value=(PAYLOAD, {"status": 200}))
    def test_parses_snapshot_fields(self, _request):
        result, _meta = quote_hf("hf_GC", "GOLD")
        quote = result["quote"]
        self.assertEqual(result["asset"]["symbol"], "GOLD")
        self.assertEqual(result["asset"]["provider_symbol"], "hf_GC")
        self.assertEqual(quote["price"], 4407.64)
        self.assertEqual(quote["previous_close"], 4407.3)
        self.assertEqual(quote["open"], 4359.4)
        self.assertEqual(quote["high"], 4444.9)
        self.assertEqual(quote["low"], 4333.0)
        self.assertIsNone(quote["volume"])
        self.assertTrue(quote["realtime"])
        self.assertIn("+08:00", quote["as_of"])
        self.assertIn(quote["market_state"], {"REGULAR", "CLOSED"})
        self.assertAlmostEqual(quote["change"], 0.34, places=6)

    @patch("scripts.providers.sina.request_bytes", return_value=(EMPTY, {"status": 200}))
    def test_empty_payload_raises(self, _request):
        with self.assertRaises(FinanceError):
            quote_hf("hf_PL", "PLATINUM")


if __name__ == "__main__":
    unittest.main()
