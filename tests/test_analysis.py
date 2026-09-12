import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.analyzers.position import analyze_position
from scripts.analyzers.technical import analyze


def bars(count=80):
    result = []
    for index in range(count):
        close = 100 + index * 0.25 + (index % 5 - 2) * 0.2
        result.append({"time": f"2026-01-{index + 1:02d}", "close": close, "open": close - .1, "high": close + .8, "low": close - .8, "volume": 1000 + index * 10})
    return result


class AnalysisTests(unittest.TestCase):
    def test_indicators_are_finite_and_explainable(self):
        result = analyze(bars(), interval="5m")
        self.assertEqual(result["status"], "ready")
        self.assertIsNotNone(result["rsi14"])
        self.assertTrue(result["signals"])
        self.assertTrue(all(level["kind"] in {"support", "resistance"} for level in result["levels"]))
        self.assertEqual(len(result["dynamic_levels"]), 4)
        self.assertEqual({level["horizon"] for level in result["dynamic_levels"]}, {"ultra_short", "short"})
        self.assertTrue(all(level["source"] and level["window_label"] for level in result["dynamic_levels"]))
        price = bars()[-1]["close"]
        self.assertTrue(all(level["value"] <= price for level in result["dynamic_levels"] if level["kind"] == "support"))
        self.assertTrue(all(level["value"] >= price for level in result["dynamic_levels"] if level["kind"] == "resistance"))

    def test_position_math(self):
        result = analyze_position(4.35, 1700, 5.839)
        self.assertAlmostEqual(result["total_cost"], 9926.3)
        self.assertAlmostEqual(result["market_value"], 7395.0)
        self.assertAlmostEqual(result["required_gain_to_break_even_pct"], 34.229885, places=5)


if __name__ == "__main__":
    unittest.main()
