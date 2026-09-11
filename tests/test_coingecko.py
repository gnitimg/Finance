import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.providers import coingecko


class CoinGeckoTests(unittest.TestCase):
    def setUp(self):
        coingecko.RESOLVED_IDS.clear()

    @patch("scripts.providers.coingecko.request_json")
    def test_configured_stablecoin_does_not_need_search_request(self, request_json):
        self.assertEqual(coingecko.resolve_coin_id("USDE"), "ethena-usde")
        request_json.assert_not_called()

    @patch("scripts.providers.coingecko.request_json")
    def test_dynamic_resolution_selects_highest_ranked_exact_symbol(self, request_json):
        request_json.return_value = ({"coins": [
            {"id": "wrong-name", "symbol": "OTHER", "market_cap_rank": 1},
            {"id": "small-abcd", "symbol": "abcd", "market_cap_rank": 700},
            {"id": "main-abcd", "symbol": "ABCD", "market_cap_rank": 120},
        ]}, {"status": 200})
        self.assertEqual(coingecko.resolve_coin_id("ABCD"), "main-abcd")
        self.assertIn("query=ABCD", request_json.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
