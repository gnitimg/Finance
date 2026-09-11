import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.symbols import normalize, yahoo_symbol


class SymbolTests(unittest.TestCase):
    def test_market_inference(self):
        self.assertEqual(normalize("auto", "601619"), ("cn", "601619"))
        self.assertEqual(normalize("auto", "00700"), ("hk", "00700"))
        self.assertEqual(normalize("auto", "nvda"), ("us", "NVDA"))
        self.assertEqual(normalize("auto", "usdt"), ("crypto", "USDT"))
        self.assertEqual(normalize("auto", "usde"), ("crypto", "USDE"))
        self.assertEqual(normalize("auto", "fdusd"), ("crypto", "FDUSD"))
        self.assertEqual(normalize("auto", "Tencent"), ("hk", "00700"))
        self.assertEqual(normalize("auto", "SPY"), ("etf", "SPY"))
        self.assertEqual(normalize("auto", "VTSAX"), ("fund", "VTSAX"))
        self.assertEqual(normalize("auto", "ES=F"), ("future", "ES=F"))
        self.assertEqual(normalize("auto", "黄金"), ("metal", "GOLD"))

    def test_alias_and_yahoo_format(self):
        self.assertEqual(normalize("auto", "腾讯"), ("hk", "00700"))
        self.assertEqual(yahoo_symbol("hk", "00700"), "0700.HK")
        self.assertEqual(yahoo_symbol("cn", "601619"), "601619.SS")
        self.assertEqual(yahoo_symbol("metal", "GOLD"), "GC=F")

    def test_rejects_unsafe(self):
        with self.assertRaises(ValueError):
            normalize("us", "NVDA; rm")


if __name__ == "__main__":
    unittest.main()
