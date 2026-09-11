from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request

from .models import FinanceError


def request_bytes(url: str, *, headers: dict | None = None, timeout: float = 10, attempts: int = 2) -> tuple[bytes, dict]:
    final_error: Exception | None = None
    merged = {"User-Agent": "gnitimg-finance/1.0 (+https://finance.gnitimg.ac.cn)"}
    merged.update(headers or {})
    for attempt in range(attempts):
        started = time.perf_counter()
        try:
            req = urllib.request.Request(url, headers=merged)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                body = response.read()
                return body, {"status": response.status, "content_type": response.headers.get("content-type"), "elapsed_ms": round((time.perf_counter() - started) * 1000, 2)}
        except urllib.error.HTTPError as exc:
            final_error = exc
            if exc.code not in {429, 500, 502, 503, 504}:
                break
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            time.sleep(min(float(retry_after or 0.5 * (2 ** attempt)), 3.0))
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            final_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.35 * (2 ** attempt))
    if isinstance(final_error, urllib.error.HTTPError):
        raise FinanceError("HTTP_ERROR", f"upstream returned HTTP {final_error.code}")
    raise FinanceError("NETWORK_ERROR", str(final_error or "request failed"))


def request_json(url: str, **kwargs) -> tuple[dict | list, dict]:
    body, meta = request_bytes(url, **kwargs)
    try:
        return json.loads(body.decode("utf-8")), meta
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FinanceError("INVALID_JSON", f"upstream returned invalid JSON: {exc}") from exc
