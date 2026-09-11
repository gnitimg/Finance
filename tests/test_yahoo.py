import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.providers.yahoo import is_closed_placeholder


class YahooBarTests(unittest.TestCase):
    def test_repeated_zero_volume_flat_bar_is_a_closed_session_placeholder(self):
        previous = {"close": 169.0, "volume": 1200}
        placeholder = {"open": 169.0, "high": 169.0, "low": 169.0, "close": 169.0, "volume": 0}
        live_move = {"open": 169.2, "high": 169.2, "low": 169.2, "close": 169.2, "volume": 0}

        self.assertTrue(is_closed_placeholder(previous, placeholder))
        self.assertFalse(is_closed_placeholder(previous, live_move))


if __name__ == "__main__":
    unittest.main()
