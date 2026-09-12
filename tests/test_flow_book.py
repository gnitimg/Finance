import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.cache import Cache
from scripts.monitoring.ml import flow_series_by_index
from scripts.providers import eastmoney
from scripts.providers.tencent import order_book_cn


class FlowSeriesTests(unittest.TestCase):
    def test_maps_only_rows_on_or_before_each_bar(self):
        flow = [{"date": "2026-01-01", "main_net": 100.0}, {"date": "2026-01-02", "main_net": -300.0}, {"date": "2026-01-05", "main_net": 900.0}]
        feats = flow_series_by_index(["2025-12-31", "2026-01-01", "2026-01-03", "2026-01-06"], flow)
        self.assertEqual(feats[0], (0.0, 0.0))
        # Single flow row: z is zero by definition, trend is the value itself
        self.assertEqual(feats[1], (0.0, 100.0))
        # 2026-01-03 still sees the 01-02 row (last row on or before the bar)
        self.assertAlmostEqual(feats[2][0], -0.7071067, places=5)
        self.assertAlmostEqual(feats[2][1], -0.5, places=5)
        self.assertAlmostEqual(feats[3][0], 1.0910895, places=5)

    def test_empty_flow_is_neutral(self):
        self.assertEqual(flow_series_by_index(["2026-01-01"], None), [(0.0, 0.0)])


class EastMoneyTests(unittest.TestCase):
    def setUp(self):
        self._previous_cache = eastmoney.CACHE
        self._directory = tempfile.TemporaryDirectory()
        eastmoney.CACHE = Cache(Path(self._directory.name) / "cache.sqlite3")

    def tearDown(self):
        eastmoney.CACHE = self._previous_cache
        self._directory.cleanup()

    @patch("scripts.providers.eastmoney.request_json")
    def test_parses_daily_rows(self, request_json):
        request_json.return_value = ({"data": {"klines": ["2026-09-11,-8539645.0,-5488420.0,14028065.0,-2540561.0,-5999084.0"]}}, {"status": 200})
        rows, _meta = eastmoney.daily_flow("cn", "601619")
        self.assertEqual(rows[0]["date"], "2026-09-11")
        self.assertEqual(rows[0]["main_net"], -8539645.0)
        self.assertEqual(rows[0]["small_net"], -5999084.0)

    def test_secid_market_prefixes(self):
        self.assertEqual(eastmoney.secid("cn", "601619"), "1.601619")
        self.assertEqual(eastmoney.secid("cn", "300750"), "0.300750")
        self.assertEqual(eastmoney.secid("hk", "00700"), "116.00700")
        self.assertIsNone(eastmoney.secid("crypto", "BTC"))


class TencentOrderBookTests(unittest.TestCase):
    def test_parses_levels_and_active_volumes(self):
        payload = (
            'v_sh601619="1~嘉泽新能~601619~4.13~4.17~4.16~319135~144970~174165~'
            "4.13~859~4.12~3505~4.11~1358~4.10~995~4.09~1289~"
            "4.14~2535~4.15~2944~4.16~4433~4.17~3658~4.18~1587~~"
            '20260911161500~-0.04~-0.96~4.16~4.04~4.13/319135/130600143~";'
        ).encode("gb18030")
        with patch("scripts.providers.tencent.request_bytes", return_value=(payload, {"status": 200})):
            book = order_book_cn("601619")
        expected_bid = 859 + 3505 + 1358 + 995 + 1289
        expected_ask = 2535 + 2944 + 4433 + 3658 + 1587
        self.assertEqual(len(book["bids"]), 5)
        self.assertEqual(len(book["asks"]), 5)
        self.assertAlmostEqual(book["imbalance"], (expected_bid - expected_ask) / (expected_bid + expected_ask), places=8)
        self.assertAlmostEqual(book["active_buy_ratio"], 144970 / (144970 + 174165), places=8)


if __name__ == "__main__":
    unittest.main()
