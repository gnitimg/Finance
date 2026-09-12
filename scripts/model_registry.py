from __future__ import annotations

import json
from pathlib import Path

from .config import DATA_DIR

CONFIG_PATH = DATA_DIR / "model_endpoints.json"
KINDS = ("chat", "rerank", "embedding")


def _load() -> dict:
    try:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("slots"), dict):
            slots = payload["slots"]
        else:
            slots = {}
    except (OSError, json.JSONDecodeError):
        slots = {}
    return {kind: (slots.get(kind) if isinstance(slots.get(kind), dict) else None) for kind in KINDS}


def _save(slots: dict) -> None:
    CONFIG_PATH.write_text(json.dumps({"slots": slots}, ensure_ascii=False, indent=2), encoding="utf-8")


def get_slot(kind: str) -> dict | None:
    """Return the slot config without the raw key when it is not needed."""
    slot = _load().get(kind)
    if slot:
        return {k: slot.get(k) for k in ("base_url", "model", "api_key", "enabled")}
    return None


def save_slot(kind: str, payload: dict) -> dict:
    if kind not in KINDS:
        return {"error": "模型类型不正确"}
    base_url = str(payload.get("base_url") or "").strip().rstrip("/")
    model = str(payload.get("model") or "").strip()
    api_key = str(payload.get("api_key") or "").strip()
    if not base_url.startswith(("http://", "https://")):
        return {"error": "Base URL 必须以 http(s) 开头"}
    if not model:
        return {"error": "模型 ID 不能为空"}
    slots = _load()
    current = slots.get(kind) or {}
    slots[kind] = {
        "base_url": base_url,
        "model": model,
        "api_key": api_key or current.get("api_key", ""),
        "enabled": bool(payload.get("enabled", False)),
    }
    _save(slots)
    return {"ok": True}


def enabled_model(kind: str) -> dict | None:
    slot = _load().get(kind)
    if slot and slot.get("enabled") and slot.get("api_key") and slot.get("base_url") and slot.get("model"):
        return slot
    return None


def clear_slot(kind: str) -> bool:
    slots = _load()
    if slots.get(kind):
        slots[kind] = None
        _save(slots)
        return True
    return False
