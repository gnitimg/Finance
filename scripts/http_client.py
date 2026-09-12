from __future__ import annotations

import http.client
import json
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request

from .models import FinanceError


def _curl_request(url: str, *, headers: dict | None = None, timeout: float = 10) -> tuple[bytes, dict]:
    """Fallback transport for hosts that drop Python's HTTP client (TLS fingerprinting)."""
    executable = shutil.which("curl")
    if not executable:
        raise FinanceError("NETWORK_ERROR", "curl fallback unavailable")
    command = [executable, "-sS", "-L", "--compressed", "--max-time", str(int(max(timeout, 1)) + 2), "-w", "\n%{http_code}"]
    merged = {"User-Agent": "gnitimg-finance/1.0 (+https://finance.gnitimg.ac.cn)"}
    merged.update(headers or {})
    for name, value in merged.items():
        command += ["-H", f"{name}: {value}"]
    command.append(url)
    try:
        completed = subprocess.run(command, capture_output=True, timeout=timeout + 4)
    except subprocess.TimeoutExpired as exc:
        raise FinanceError("NETWORK_ERROR", "curl fallback timed out") from exc
    stdout = completed.stdout
    split = stdout.rfind(b"\n")
    body, status_text = stdout[:split], stdout[split + 1 :].strip()
    if completed.returncode != 0 or not status_text.isdigit():
        detail = completed.stderr.decode("utf-8", errors="replace").strip()[:140]
        raise FinanceError("NETWORK_ERROR", f"curl fallback failed: {detail}")
    status = int(status_text)
    if status >= 400:
        raise FinanceError("HTTP_ERROR", f"upstream returned HTTP {status}")
    return body, {"status": status, "content_type": None, "elapsed_ms": None, "transport": "curl"}


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
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, socket.timeout, ConnectionError) as exc:
            final_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.35 * (2 ** attempt))
    if isinstance(final_error, urllib.error.HTTPError):
        raise FinanceError("HTTP_ERROR", f"upstream returned HTTP {final_error.code}")
    if isinstance(final_error, (urllib.error.URLError, http.client.HTTPException, TimeoutError, socket.timeout, ConnectionError)):
        # Hosts that fingerprint TLS drop urllib before any HTTP status exists;
        # give the system curl a single chance before giving up.
        try:
            return _curl_request(url, headers=headers, timeout=timeout)
        except FinanceError:
            pass
    raise FinanceError("NETWORK_ERROR", str(final_error or "request failed"))


def request_json(url: str, **kwargs) -> tuple[dict | list, dict]:
    body, meta = request_bytes(url, **kwargs)
    try:
        return json.loads(body.decode("utf-8")), meta
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FinanceError("INVALID_JSON", f"upstream returned invalid JSON: {exc}") from exc


def _curl_post(url: str, form: dict, headers: dict | None = None, timeout: float = 10) -> tuple[bytes, dict]:
    executable = shutil.which("curl")
    if not executable:
        raise FinanceError("NETWORK_ERROR", "curl fallback unavailable")
    command = [executable, "-sS", "-L", "--compressed", "--max-time", str(int(max(timeout, 1)) + 2), "-w", "\n%{http_code}", "-X", "POST"]
    merged = {"User-Agent": "gnitimg-finance/1.0 (+https://finance.gnitimg.ac.cn)"}
    merged.update(headers or {})
    for name, value in merged.items():
        command += ["-H", f"{name}: {value}"]
    for name, value in form.items():
        command += ["--data-urlencode", f"{name}={value}"]
    command.append(url)
    try:
        completed = subprocess.run(command, capture_output=True, timeout=timeout + 4)
    except subprocess.TimeoutExpired as exc:
        raise FinanceError("NETWORK_ERROR", "curl POST fallback timed out") from exc
    stdout = completed.stdout
    split = stdout.rfind(b"\n")
    resp_body, status_text = stdout[:split], stdout[split + 1 :].strip()
    if completed.returncode != 0 or not status_text.isdigit():
        detail = completed.stderr.decode("utf-8", errors="replace").strip()[:140]
        raise FinanceError("NETWORK_ERROR", f"curl POST failed: {detail}")
    status = int(status_text)
    if status >= 400:
        raise FinanceError("HTTP_ERROR", f"upstream returned HTTP {status}")
    return resp_body, {"status": status, "content_type": None, "elapsed_ms": None, "transport": "curl"}


def request_post_json(url: str, form: dict, *, headers: dict | None = None, timeout: float = 10, attempts: int = 1) -> tuple[dict, dict]:
    """POST a urlencoded form and parse the JSON reply, with the same curl
    fallback as the GET path."""
    import urllib.parse

    merged = {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}
    merged.update(headers or {})
    encoded = urllib.parse.urlencode(form).encode("utf-8")
    final_error: Exception | None = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, data=encoded, headers=merged, method="POST")
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
                # Anti-bot frontends may answer 200 with an HTML challenge;
                # treat any non-JSON body as a transport failure and fall back.
                return json.loads(body), {"status": response.status, "transport": "urllib"}
        except (json.JSONDecodeError, urllib.error.URLError, http.client.HTTPException, TimeoutError, socket.timeout, ConnectionError) as exc:
            final_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.35 * (2 ** attempt))
    try:
        body, meta = _curl_post(url, form, headers=merged, timeout=timeout)
        return json.loads(body.decode("utf-8")), meta
    except (FinanceError, json.JSONDecodeError):
        pass
    raise FinanceError("NETWORK_ERROR", str(final_error or "POST request failed"))
