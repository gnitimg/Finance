from __future__ import annotations

import json
import os
import re
import urllib.request

from ..config import env_bool, read_json
from ..models import FinanceError
from .privacy import sanitize

SYSTEM_PROMPT = """You are a finance synthesis specialist. Use only the supplied verified deterministic JSON. Do not fetch or calculate market data. If evidence is missing, say so. Return one JSON object with keys summary, assessment, bull_case, bear_case, key_risks, key_uncertainties, watch_conditions, confidence, evidence. Never give execution instructions or claim certainty."""


def _parse(content: str) -> dict:
    text = content.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S | re.I)
    if fenced:
        text = fenced.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise FinanceError("SPECIALIST_INVALID_JSON", "Specialist returned invalid JSON", "specialist") from exc
    if not isinstance(parsed, dict):
        raise FinanceError("SPECIALIST_INVALID_SCHEMA", "Specialist result must be an object", "specialist")
    return parsed


def analyze(payload: dict) -> tuple[dict, dict]:
    if not env_bool("FINANCE_SPECIALIST_ENABLED", False):
        raise FinanceError("SPECIALIST_DISABLED", "Finance specialist is disabled", "specialist")
    provider = os.getenv("FINANCE_SPECIALIST_PROVIDER", "opencode_zen").strip()
    policy = read_json("model-policy.json")
    provider_policy = (policy.get("providers") or {}).get(provider)
    if not provider_policy:
        raise FinanceError("SPECIALIST_PROVIDER", f"Unsupported specialist provider: {provider}", "specialist")
    model = os.getenv("FINANCE_SPECIALIST_MODEL", "").strip()
    allowed = provider_policy.get("allowed_models") or []
    if not model or model not in allowed:
        raise FinanceError("MODEL_POLICY", "Configured specialist model is absent from the explicit allowlist", "specialist")
    key_name = "GOOGLE_API_KEY" if provider == "google" else "OPENCODE_ZEN_API_KEY"
    api_key = os.getenv(key_name, "").strip()
    if not api_key:
        raise FinanceError("MISSING_API_KEY", f"{key_name} is not configured", "specialist")
    safe_payload = sanitize(payload)
    body = json.dumps({
        "model": model,
        "temperature": 0.15,
        "max_tokens": 1200,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(safe_payload, ensure_ascii=False)},
        ],
    }, ensure_ascii=False).encode("utf-8")
    timeout = max(3, min(int(os.getenv("FINANCE_SPECIALIST_TIMEOUT", "30")), 60))
    request = urllib.request.Request(provider_policy["endpoint"], data=body, method="POST", headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": "gnitimg-finance/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
        content = result["choices"][0]["message"]["content"]
    except Exception as exc:
        raise FinanceError("SPECIALIST_UNAVAILABLE", f"Specialist request failed: {type(exc).__name__}", provider) from exc
    return _parse(content), {"provider": provider, "model": model}
