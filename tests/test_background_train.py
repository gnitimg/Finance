import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.background_train import restricted_window


class BackgroundTrainingWindowTests(unittest.TestCase):
    def test_heavy_refresh_is_deferred_during_a_share_session(self):
        monday = datetime(2026, 9, 14)
        self.assertFalse(restricted_window(monday.replace(hour=8, minute=54)))
        self.assertTrue(restricted_window(monday.replace(hour=8, minute=55)))
        self.assertTrue(restricted_window(monday.replace(hour=15, minute=5)))
        self.assertFalse(restricted_window(monday.replace(hour=15, minute=6)))

    def test_weekend_never_blocks_requested_retraining(self):
        saturday = datetime(2026, 9, 12, 10, 0)
        self.assertFalse(restricted_window(saturday))


if __name__ == "__main__":
    unittest.main()
