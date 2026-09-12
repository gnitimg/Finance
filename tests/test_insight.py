import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import scripts.model_registry as model_registry
from scripts import insight
from scripts.models import FinanceError


def synthetic_bars(count=80):
    from datetime import datetime, timedelta, timezone
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bars = []
    for i in range(count):
        moment = started + timedelta(days=i)
        price = 100 + i * 0.2
        bars.append({"time": moment.isoformat().replace("+00:00", "Z"), "timestamp": int(moment.timestamp()), "close": price, "open": price, "high": price + 0.5, "low": price - 0.5, "volume": 1000})
    return bars


class InsightTests(unittest.TestCase):
    def setUp(self):
        self._registry_dir = tempfile.TemporaryDirectory()
        self._original_path = model_registry.CONFIG_PATH
        model_registry.CONFIG_PATH = Path(self._registry_dir.name) / "model_endpoints.json"
        model_registry.save_slot("chat", {"base_url": "https://llm.example.com/v1", "model": "test-chat", "api_key": "sk-test", "enabled": True})

    def tearDown(self):
        model_registry.CONFIG_PATH = self._original_path
        self._registry_dir.cleanup()

    def test_requires_configured_chat_model(self):
        model_registry.clear_slot("chat")
        with self.assertRaises(FinanceError) as ctx:
            insight.generate_insight("cn", "601619", None)
        self.assertEqual(ctx.exception.code, "NOT_CONFIGURED")

    def test_tool_loop_gathers_real_data_and_parses_final(self):
        rounds = {"n": 0}

        def fake_llm(endpoint, model, key, messages):
            rounds["n"] += 1
            if rounds["n"] == 1:
                return {"choices": [{"message": {"content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "analyze", "arguments": json.dumps({"market": "cn", "symbol": "601619"})}}]}}]}
            return {"choices": [{"message": {"content": json.dumps({"headline": "测试结论", "reading": "测试解读", "risks": ["r1"], "uncertainty": "u1"})}}]}

        with mock.patch.object(insight, "_call_llm", fake_llm), \
             mock.patch.object(insight, "_execute_tool", return_value={"quote": {"price": 4.13}}):
            result = insight.generate_insight("cn", "601619", None)
        self.assertEqual(result["headline"], "测试结论")
        self.assertEqual(result["tools_used"], ["analyze"])
        self.assertTrue(result["grounded"])
        self.assertIn("不确定性", result["disclaimer"])

    def test_tool_loop_rounds_are_bounded(self):
        def fake_llm(endpoint, model, key, messages):
            return {"choices": [{"message": {"content": None, "tool_calls": [{"id": "c", "type": "function", "function": {"name": "health", "arguments": "{}"}}]}}]}

        with mock.patch.object(insight, "_call_llm", fake_llm), \
             mock.patch.object(insight, "_execute_tool", return_value={"ok": True}):
            with self.assertRaises(FinanceError) as ctx:
                insight.generate_insight("cn", "601619", None)
        self.assertEqual(ctx.exception.code, "INSIGHT_INCOMPLETE")

    def test_invalid_range_falls_back_to_defaults(self):
        with mock.patch("scripts.finance.analyze_asset", side_effect=[ValueError("bad pair"), {"data": {"asset": {"symbol": "601619"}}}]) as mocked, \
             mock.patch.object(insight, "_clip_analysis", return_value={"quote": {"price": 4.13}}):
            result = insight._execute_tool("analyze", {"market": "cn", "symbol": "601619", "range": "1d", "interval": "30m"}, "cn", "601619")
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(mocked.call_args_list[1].args[:2], ("cn", "601619"))
        self.assertNotIn("range", mocked.call_args_list[1].kwargs)
        self.assertEqual(result, {"quote": {"price": 4.13}})


if __name__ == "__main__":
    unittest.main()
