import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.specialist.privacy import sanitize


class PrivacyTests(unittest.TestCase):
    def test_sensitive_fields_and_patterns_are_removed(self):
        value = sanitize({"telegram_id": "123", "query": "电话 13800138000 api_key=sk-secretsecretsecret", "account_id": "ABC12345", "market": {"price": 10}})
        self.assertNotIn("telegram_id", value)
        self.assertNotIn("account_id", value)
        self.assertNotIn("13800138000", value["query"])
        self.assertNotIn("sk-secret", value["query"])
        self.assertEqual(value["market"]["price"], 10)


if __name__ == "__main__":
    unittest.main()
