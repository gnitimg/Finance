from __future__ import annotations

import re

PATTERNS = [
    (re.compile(r"\b(?:oc_sk|sk|AIza)[-_A-Za-z0-9]{12,}\b"), "[REDACTED_KEY]"),
    (re.compile(r"(?i)\b(?:api[_ -]?key|token|secret|authorization)\s*[:=]\s*\S+"), "[REDACTED_SECRET]"),
    (re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)"), "[REDACTED_PHONE]"),
    (re.compile(r"(?i)\b(?:telegram|weixin|wechat)[ _-]?(?:user)?[ _-]?id\s*[:=]?\s*[-\w]+"), "[REDACTED_USER_ID]"),
    (re.compile(r"(?i)\b(?:account|账户|账号)[ _-]?(?:id|number|号)?\s*[:=：]?\s*[A-Za-z0-9-]{5,}"), "[REDACTED_ACCOUNT]"),
]


def redact_text(text: str) -> str:
    result = text
    for pattern, replacement in PATTERNS:
        result = pattern.sub(replacement, result)
    return result[:4000]


def sanitize(value):
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, dict):
        blocked = {"user_id", "telegram_id", "weixin_id", "account_id", "api_key", "token", "secret", "authorization"}
        return {key: sanitize(item) for key, item in value.items() if key.lower() not in blocked}
    return value
