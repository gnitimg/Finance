import math
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.backtest import MARKET_RULES, _buy_cost, _quantity_for, _sell_cost, run


def trending(count=300, drift=0.004):
    started = datetime(2024, 1, 1, tzinfo=timezone.utc)
    bars = []
    price = 50.0
    for index in range(count):
        day = started + timedelta(days=index)
        if day.weekday() >= 5:
            continue
        open_price = price
        price *= 1 + drift * (1 if index % 7 else -0.2)
        bars.append({
            "time": day.isoformat(),
            "timestamp": day.timestamp(),
            "open": round(open_price, 4), "close": round(price, 4),
            "high": round(max(open_price, price) * 1.002, 4),
            "low": round(min(open_price, price) * 0.998, 4),
            "volume": 1_000_000,
        })
    return bars


class RuleTests(unittest.TestCase):
    def test_cn_fee_schedule(self):
        rules = MARKET_RULES["cn"]
        self.assertEqual(_buy_cost(rules, 1000.0), 5.0)  # min 5 CNY applies
        self.assertAlmostEqual(_buy_cost(rules, 100_000.0), 50.0, places=6)
        sell = _sell_cost(rules, 100_000.0)
        self.assertAlmostEqual(sell, 50.0 + 50.0, places=6)  # commission + stamp

    def test_round_lot_quantity(self):
        rules = MARKET_RULES["cn"]
        self.assertEqual(_quantity_for(rules, 100_000.0, 33.33), 2900)
        self.assertEqual(_quantity_for(rules, 3000.0, 33.33), 0)  # below one lot + fees
        us = MARKET_RULES["us"]
        self.assertEqual(_quantity_for(us, 1000.0, 33.0), 30)


class SimulationTests(unittest.TestCase):
    def test_trending_series_produces_valid_trade_log(self):
        bars = trending()
        report = run(bars, "cn", "600000", "1d", capital=1_000_000)
        self.assertEqual(report["status"], "ready")
        buys = {}
        for trade in report.get("recent_trades", []):
            day = str(trade.get("time", ""))[:10]
            if trade["side"] == "buy":
                buys[day] = buys.get(day, 0) + 1
            else:
                # T+1: a sell may never settle on the day the shares were bought
                self.assertEqual(buys.get(day, 0), 0, f"same-day sell on {day}")
        self.assertIsNotNone(report["final_equity"])
        self.assertLess(report["fees_paid"], 50_000)

    def test_insufficient_data_reported(self):
        report = run(trending(60), "cn", "600000", "1d")
        self.assertEqual(report["status"], "insufficient_data")


if __name__ == "__main__":
    unittest.main()
