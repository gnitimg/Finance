import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.routing import classify


class RoutingTests(unittest.TestCase):
    def test_levels(self):
        self.assertEqual(classify("腾讯多少钱")["level"], "L0")
        self.assertEqual(classify("腾讯 RSI 是多少")["level"], "L1")
        self.assertEqual(classify("腾讯技术面怎么样")["level"], "L1")
        self.assertEqual(classify("腾讯和阿里哪个风险收益更好", 2)["level"], "L2")
        self.assertEqual(classify("如果腾讯跌破 300 怎么看")["level"], "L2")
        self.assertEqual(classify("深入分析腾讯")["level"], "L2")

    def test_causal_requires_event_data(self):
        route = classify("NVDA 为什么上涨")
        self.assertTrue(route["causal_reasoning"])
        self.assertFalse(route["specialist_recommended"])


if __name__ == "__main__":
    unittest.main()
