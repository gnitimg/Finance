import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.analyzers.stablecoin import analyze


class StablecoinTests(unittest.TestCase):
    @patch("scripts.analyzers.stablecoin.quote")
    def test_price_only_stablecoin_returns_partial_risk_without_penalty(self, quote):
        quote.return_value = {
            "asset": {"market": "crypto", "symbol": "USDE", "type": "stablecoin"},
            "quote": {"price": 1.001, "source": "CoinGecko"},
        }
        result = analyze("USDE")
        self.assertEqual(result["coverage"]["level"], "partial")
        self.assertIsNone(result["risk"]["components"]["liquidity"])
        self.assertAlmostEqual(result["risk"]["score"], 5.0)


if __name__ == "__main__":
    unittest.main()
