from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from .config import DATA_DIR

CONFIG_PATH = DATA_DIR / "model_endpoints.json"
KINDS = ("chat", "rerank", "embedding")


def _load() -> dict:
    try:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("models"), list):
            return payload
    except (OSError, json.JSONDecodeError):
        pass
    return {"models": []}


def _save(payload: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _mask(key: str) -> str:
    if not key:
        return ""
    return f"{key[:4]}****{key[-3:]}" if len(key) > 9 else "****"


def _validate(entry: dict) -> str | None:
    if not re.fullmatch(r"[\w\u4e00-\u9fff \-]{1,40}", str(entry.get("name") or "")):
        return "模型名称需为 1-40 位中文/字母/数字/下划线"
    if entry.get("kind") not in KINDS:
        return "模型类型必须是 chat / rerank / embedding"
    url = str(entry.get("base_url") or "")
    if not re.fullmatch(r"https?://[\w.\-]+(:\d+)?(/[\w./\-]*)?", url.rstrip("/")):
        return "Base URL 必须是合法的 http(s) 地址"
    if not re.fullmatch(r"[\w.\-/]{1,80}", str(entry.get("model") or "")):
        return "模型 ID 格式不正确"
    return None


def list_models(mask_keys: bool = True) -> list[dict]:
    models = []
    for entry in _load()["models"]:
        item = {k: entry.get(k) for k in ("id", "name", "kind", "base_url", "model", "enabled")}
        item["has_key"] = bool(entry.get("api_key"))
        item["api_key"] = _mask(entry.get("api_key") or "") if mask_keys else entry.get("api_key")
        models.append(item)
    return models


def upsert_model(payload: dict) -> dict | None:
    error = _validate(payload)
    if error:
        return {"error": error}
    registry = _load()
    entry_id = str(payload.get("id") or "") or uuid.uuid4().hex[:12]
    existing = next((m for m in registry["models"] if m.get("id") == entry_id), None)
    api_key = str(payload.get("api_key") or "").strip()
    entry = {
        "id": entry_id,
        "name": str(payload["name"]).strip(),
        "kind": payload["kind"],
        "base_url": str(payload["base_url"]).strip().rstrip("/"),
        "model": str(payload["model"]).strip(),
        "api_key": api_key if api_key else (existing or {}).get("api_key", ""),
        "enabled": bool(payload.get("enabled", True)),
    }
    if existing:
        registry["models"][registry["models"].index(existing)] = entry
    else:
        registry["models"].append(entry)
    _save(registry)
    return {"id": entry["id"]}


def delete_model(entry_id: str) -> bool:
    registry = _load()
    before = len(registry["models"])
    registry["models"] = [m for m in registry["models"] if m.get("id") != entry_id]
    if len(registry["models"]) != before:
        _save(registry)
        return True
    return False


def enabled_model(kind: str) -> dict | None:
    """First enabled user model of a kind, for provider auto-detection."""
    for entry in _load()["models"]:
        if entry.get("kind") == kind and entry.get("enabled") and entry.get("api_key"):
            return entry
    return None
