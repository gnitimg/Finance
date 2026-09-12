import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.cache import Cache
from scripts.monitoring.ml import flow_series_by_index
from scripts.providers import eastmoney
from scripts.providers.sina import quote_gb, quote_hk
from scripts.providers.tencent import order_book_cn

GB_PAYLOAD = (
    'var hq_str_gb_nvda="英伟达,218.2900,-0.03,2026-09-12 09:49:31,-0.0700,221.2350,222.0000,218.1500,'
    '236.2900,164.0200,89060140,146731240,5260789014625,6.57,33.230000,0.00,0.00,0.00,0.00,24100000067,69,'
    '218.2600,-0.01,-0.03,Sep 11 08:01PM EDT,Sep 11 04:00PM EDT,218.3600,5860128,1,2026,19551225805.0000,'
    '232.0498,214.9710,1279734687.8914,218.2900,218.3600";'
).encode("gb18030")

HK_PAYLOAD = (
    'var hq_str_hk00700="TENCENT,腾讯控股,419.400,425.600,430.800,419.400,428.400,2.800,0.658,428.39999,'
    '428.79999,6674835081,15628379,0.000,0.000,675.134,411.000,2026/09/11,16:09";'
).encode("gb18030")


class SinaGlobalTests(unittest.TestCase):
    @patch("scripts.providers.sina.request_bytes", return_value=(GB_PAYLOAD, {"status": 200}))
    def test_parses_us_snapshot(self, _request):
        result, _meta = quote_gb("NVDA")
        quote = result["quote"]
        self.assertEqual(quote["price"], 218.29)
        self.assertEqual(quote["previous_close"], 218.36)
        self.assertEqual(quote["open"], 221.235)
        self.assertIsNone(result["asset"]["exchange"])
        self.assertTrue(quote["realtime"])
        self.assertIn(quote["market_state"], {"REGULAR", "CLOSED"})

    @patch("scripts.providers.sina.request_bytes", return_value=(HK_PAYLOAD, {"status": 200}))
    def test_parses_hk_snapshot(self, _request):
        result, _meta = quote_hk("00700")
        quote = result["quote"]
        self.assertEqual(quote["price"], 428.4)
        self.assertEqual(quote["previous_close"], 425.6)
        self.assertAlmostEqual(quote["change_pct"], 0.6578947, places=4)
        self.assertEqual(result["asset"]["currency"], "HKD")


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

    @patch("scripts.providers.eastmoney._fetch_json")
    def test_industry_mapping_uses_seven_day_cache(self, fetch):
        fetch.return_value = {"data": {"f127": "电力行业"}}
        self.assertEqual(eastmoney.stock_industry("cn", "601619"), "电力行业")
        self.assertEqual(eastmoney.stock_industry("cn", "601619"), "电力行业")
        fetch.assert_called_once()
        self.assertIn("secid=1.601619", fetch.call_args.args[0])
        self.assertEqual(eastmoney.INDUSTRY_TTL, 7 * 86_400)

    @patch("scripts.providers.eastmoney._fetch_json")
    def test_sector_kline_cache_and_twenty_day_momentum(self, fetch):
        rows = [f"2026-01-{index + 1:02d},{index + 1}" for index in range(21)]
        fetch.return_value = {"data": {"klines": rows}}
        first = eastmoney.kline("90.BK0001", eastmoney.SECTOR_KLINE_LIMIT)
        second = eastmoney.kline("90.BK0001", eastmoney.SECTOR_KLINE_LIMIT)
        enriched = eastmoney._enrich_series(first)
        self.assertEqual(second, first)
        fetch.assert_called_once()
        self.assertAlmostEqual(enriched[-1]["momentum_20"], 20.0)
        self.assertAlmostEqual(enriched[-1]["return_1"], .05)

    @patch("scripts.providers.eastmoney._fetch_json", side_effect=AssertionError("network must not run"))
    def test_cached_sector_helpers_never_fetch(self, _fetch):
        eastmoney.CACHE.set("sectorctx:601619", {"industry": "电力行业", "board_code": "BK0001", "source": "East Money"})
        board = [{"date": "2026-01-01", "close": 100.0}, {"date": "2026-01-02", "close": 101.0}]
        index = [{"date": "2026-01-01", "close": 200.0}, {"date": "2026-01-02", "close": 202.0}]
        limit = eastmoney.SECTOR_KLINE_LIMIT
        eastmoney.CACHE.set(f"kline:90.BK0001:{limit}", board)
        eastmoney.CACHE.set(f"kline:1.000300:{limit}", index)
        context = eastmoney.cached_sector_context("cn", "601619")
        reference = eastmoney.cached_market_reference(context["board_code"])
        self.assertEqual(context["industry"], "电力行业")
        self.assertAlmostEqual(reference[0][-1]["return_1"], .01)
        self.assertAlmostEqual(reference[1][-1]["return_1"], .01)

    @patch("scripts.providers.eastmoney._fetch_json", side_effect=ConnectionError("blocked"))
    def test_kline_failure_opens_cooldown_before_second_request(self, fetch):
        with self.assertRaises(ConnectionError):
            eastmoney.kline("90.BK0001", eastmoney.SECTOR_KLINE_LIMIT)
        with self.assertRaises(eastmoney.FinanceError) as raised:
            eastmoney.kline("1.000300", eastmoney.SECTOR_KLINE_LIMIT)
        self.assertEqual(raised.exception.code, "PROVIDER_COOLDOWN")
        fetch.assert_called_once()


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
