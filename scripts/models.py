from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def clean_json(value: Any) -> Any:
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return round(value, 8)
    if isinstance(value, dict):
        return {str(k): clean_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_json(v) for v in value]
    return value


class FinanceError(Exception):
    def __init__(self, code: str, message: str, provider: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.provider = provider

    def as_dict(self) -> dict:
        result = {"code": self.code, "message": self.message}
        if self.provider:
            result["provider"] = self.provider
        return result
