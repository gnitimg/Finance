from __future__ import annotations

import re

from ..http_client import request_bytes
from ..models import FinanceError


def order_book_cn(symbol: str) -> dict:
    """Tencent five-level order book plus active buy/sell volumes for an A-share.

    Volumes arrive in lots (手). The imbalance and active-buy ratio are
    unit-free and feed the live context of the forecast.
    """
    prefix = "sh" if symbol.startswith(("5", "6", "9")) else "sz"
    body, _ = request_bytes(
        f"https://qt.gtimg.cn/q={prefix}{symbol}",
        headers={"Referer": "https://gu.qq.com/"},
        timeout=6,
        attempts=1,
    )
    text = body.decode("gb18030", errors="replace")
    if "none_match" in text or '=""' in text:
        raise FinanceError("NO_DATA", f"Tencent returned no order book for {symbol}", "tencent")
    match = re.search(r'="(.*)"', text)
    if not match:
        raise FinanceError("INVALID_SCHEMA", "Tencent response is incomplete", "tencent")
    fields = match.group(1).split("~")
    if len(fields) < 31:
        raise FinanceError("INVALID_SCHEMA", "Tencent response is incomplete", "tencent")
    try:
        bids = [(float(fields[9 + i * 2]), float(fields[10 + i * 2])) for i in range(5)]
        asks = [(float(fields[19 + i * 2]), float(fields[20 + i * 2])) for i in range(5)]
        outer_lots = float(fields[7])
        inner_lots = float(fields[8])
    except (ValueError, IndexError) as exc:
        raise FinanceError("INVALID_SCHEMA", "Tencent order book contains invalid numbers", "tencent") from exc
    bid_volume = sum(volume for _price, volume in bids if volume > 0)
    ask_volume = sum(volume for _price, volume in asks if volume > 0)
    total_book = bid_volume + ask_volume
    total_active = outer_lots + inner_lots
    return {
        "bids": bids,
        "asks": asks,
        "imbalance": (bid_volume - ask_volume) / total_book if total_book else 0.0,
        "active_buy_ratio": outer_lots / total_active if total_active else 0.5,
        "as_of": fields[30] or None,
    }
