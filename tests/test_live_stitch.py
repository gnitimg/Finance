import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.providers.service import stitch_live_bars


def bars(volumes):
    result = []
    base = 1_700_003_700  # aligned to a 5-minute slot boundary
    for index, volume in enumerate(volumes):
        stamp = base + index * 300
        result.append({"time": f"t{index}", "timestamp": stamp, "close": 10 + index, "open": 10 + index, "high": 10.5 + index, "low": 9.5 + index, "adjusted_close": 10 + index, "volume": volume})
    return result


class LiveStitchTests(unittest.TestCase):
    def test_forming_intraday_bar_takes_live_price_and_residual_volume(self):
        result = {"history": bars([100, 110, 120, 130, 140])}
        live = {"quote": {"price": 17.2, "volume": 610, "high": None, "low": None, "as_of": 1_700_003_700 + 4 * 300 + 120}}
        stitch_live_bars(result, live, "5m")
        last = result["history"][-1]
        self.assertEqual(last["close"], 17.2)
        self.assertEqual(last["high"], 17.2)
        self.assertEqual(last["low"], 13.5)
        self.assertAlmostEqual(last["volume"], 610 - (100 + 110 + 120 + 130), places=6)
        self.assertEqual(result["history"][0]["volume"], 100)
        self.assertTrue(any("Forming bar" in warning for warning in result.get("warnings", [])))

    def test_completed_bar_is_never_rewritten(self):
        result = {"history": bars([100, 110, 120])}
        live = {"quote": {"price": 99.0, "volume": 999, "high": None, "low": None, "as_of": 1_700_003_700 + 9 * 300}}
        stitch_live_bars(result, live, "5m")
        last = result["history"][-1]
        self.assertEqual(last["close"], 12)
        self.assertEqual(last["volume"], 120)

    def test_daily_bar_takes_day_ohlc_and_cumulative_volume(self):
        result = {"history": bars([100, 110])}
        live = {"quote": {"price": 15.0, "volume": 250, "high": 15.4, "low": 9.2, "as_of": 1_700_003_700}}
        stitch_live_bars(result, live, "1d")
        last = result["history"][-1]
        self.assertEqual(last["close"], 15.0)
        self.assertEqual(last["high"], 15.4)
        self.assertEqual(last["low"], 9.2)
        self.assertEqual(last["volume"], 250)


if __name__ == "__main__":
    unittest.main()
