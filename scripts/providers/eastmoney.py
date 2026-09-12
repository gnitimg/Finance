from __future__ import annotations

from datetime import datetime, timedelta, timezone

import re
import time

from ..cache import CACHE
from ..http_client import request_json, request_post_json
from ..models import FinanceError, utc_now

# East Money's public fund-flow feed. The push2his daykline endpoint keeps the
# latest ~120 trading days; the last row carries today's running value while
# the session is open. secid prefixes: 1/0 = SH/SZ, 116 = HK, 105/106/107 = US.
_HISTORY_URL = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
_US_MARKETS = ("105", "106", "107")
_US_RESOLVED: dict[str, str] = {}
_BREAKER_KEY = "flow:eastmoney:cooldown"
_KLINE_BREAKER_KEY = "sector:eastmoney:kline-cooldown"
_COOLDOWN_SECONDS = 900
INDUSTRY_TTL = 7 * 86_400
BOARD_DIRECTORY_TTL = 300
SECTOR_CONTEXT_TTL = 300
SECTOR_KLINE_TTL = 1800
SECTOR_STALE_TTL = 7 * 86_400
SECTOR_KLINE_LIMIT = 1200


def _cooldown_until() -> float:
    marker, _ = CACHE.get(_BREAKER_KEY, 7200)
    return float((marker or {}).get("until") or 0)


def secid(market: str, symbol: str) -> str | None:
    if market == "cn":
        return ("1." if symbol.startswith(("6", "9", "5")) else "0.") + symbol
    if market == "hk":
        return "116." + symbol
    if market == "us":
        resolved = _US_RESOLVED.get(symbol)
        return (resolved or "105.") + symbol
    return None


def _parse_rows(payload) -> list[dict]:
    data = payload.get("data") if isinstance(payload, dict) else None
    klines = (data or {}).get("klines") or []
    rows = []
    for line in klines:
        parts = str(line).split(",")
        try:
            rows.append({
                "date": parts[0],
                "main_net": float(parts[1]),
                "super_net": float(parts[2]) if len(parts) > 2 else None,
                "big_net": float(parts[3]) if len(parts) > 3 else None,
                "medium_net": float(parts[4]) if len(parts) > 4 else None,
                "small_net": float(parts[5]) if len(parts) > 5 else None,
            })
        except (ValueError, IndexError):
            continue
    return rows


def daily_flow(market: str, symbol: str, limit: int = 120) -> tuple[list[dict], dict]:
    base = secid(market, symbol)
    if not base:
        raise FinanceError("UNSUPPORTED_MARKET", f"No fund-flow coverage for {market}", "eastmoney")
    cache_key = f"flow:{market}:{symbol}"
    cached, cache_meta = CACHE.get(cache_key, 600)
    if cached:
        return cached, {"transport": "cache", "elapsed_ms": None}
    if time.time() < _cooldown_until():
        raise FinanceError("PROVIDER_COOLDOWN", "East Money skipped during failure cooldown", "eastmoney")
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36", "Referer": "https://quote.eastmoney.com/"}
    # US listings sit on 105/106/107 depending on exchange; remember the code
    # that returns data so later requests go straight to it.
    candidates = [base]
    if market == "us" and base.startswith("105."):
        candidates = [code + "." + symbol for code in _US_MARKETS]
    for identifier in candidates:
        url = f"{_HISTORY_URL}?lmt={max(2, min(int(limit), 120))}&klt=101&secid={identifier}&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56"
        payload, http_meta = request_json(url, timeout=8, attempts=2, headers=headers)
        rows = _parse_rows(payload)
        if rows:
            if market == "us":
                _US_RESOLVED[symbol] = identifier.split(".", 1)[0]
            CACHE.set(cache_key, rows)
            return rows, http_meta
    failures, _ = CACHE.get("flow:eastmoney:failures", 86400) or ({"count": 0}, {})
    count = int((failures or {}).get("count") or 0) + 1
    CACHE.set("flow:eastmoney:failures", {"count": count})
    if count >= 2:
        CACHE.set(_BREAKER_KEY, {"until": time.time() + _COOLDOWN_SECONDS})
        CACHE.set("flow:eastmoney:failures", {"count": 0})
    raise FinanceError("NO_DATA", f"East Money returned no fund flow for {market}:{symbol}", "eastmoney")


def cached_daily_flow(market: str, symbol: str, max_age: int = 1800) -> list[dict] | None:
    """Read optional flow context without delaying an interactive analysis."""
    cached, _cache_meta = CACHE.get(f"flow:{market}:{symbol}", max_age)
    return cached


_DELAY_URL = "https://push2delay.eastmoney.com/api/qt"
_HIS_URL = "https://push2his.eastmoney.com/api/qt"
_BROWSER_HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36", "Referer": "https://quote.eastmoney.com/"}


def _fetch_json(url: str) -> dict:
    # push2delay accepts urllib but push2his drops it mid-TLS; the shared
    # client's curl fallback covers both without leaking that detail here.
    payload, _meta = request_json(url, headers=_BROWSER_HEADERS, timeout=10, attempts=1)
    return payload if isinstance(payload, dict) else {}


def stock_industry(market: str, symbol: str) -> str | None:
    identifier = secid(market, symbol)
    if not identifier:
        return None
    cache_key = f"industry:{market}:{symbol}"
    cached, _meta = CACHE.get(cache_key, INDUSTRY_TTL)
    if isinstance(cached, dict) and "industry" in cached:
        return cached.get("industry")
    url = f"{_DELAY_URL}/stock/get?secid={identifier}&fields=f127"
    try:
        payload = _fetch_json(url)
    except Exception:
        stale, _stale_meta = CACHE.get(cache_key, 0, SECTOR_STALE_TTL)
        return stale.get("industry") if isinstance(stale, dict) else None
    industry = (payload.get("data") or {}).get("f127") or None
    if industry:
        CACHE.set(cache_key, {"industry": industry})
    return industry


def board_directory() -> list[dict]:
    """Industry boards sorted by change_pct desc, with main-force inflow."""
    cache_key = "sector:board-directory"
    cached, _meta = CACHE.get(cache_key, BOARD_DIRECTORY_TTL)
    if isinstance(cached, list):
        return cached
    try:
        payload = _fetch_json(f"{_DELAY_URL}/clist/get?pn=1&pz=500&po=1&np=1&fltt=2&fid=f3&fs=m:90+t:2&fields=f12,f14,f3,f62")
    except Exception:
        stale, _stale_meta = CACHE.get(cache_key, 0, 86_400)
        if isinstance(stale, list):
            return stale
        raise
    rows = ((payload.get("data") or {}).get("diff")) or []
    directory = [{"code": r.get("f12"), "name": r.get("f14"), "change_pct": r.get("f3"), "main_net": r.get("f62")} for r in rows if r.get("f12")]
    if directory:
        CACHE.set(cache_key, directory)
    return directory


def kline(identifier: str, limit: int = 120) -> list[dict]:
    """Daily closes for an index or board identifier (e.g. 1.000300, 90.BK0428)."""
    limit = max(2, min(int(limit), 1500))
    cache_key = f"kline:{identifier}:{limit}"
    cached, _meta = CACHE.get(cache_key, SECTOR_KLINE_TTL)
    if cached is not None and isinstance(cached, list):
        return cached
    cooldown, _cooldown_meta = CACHE.get(_KLINE_BREAKER_KEY, SECTOR_KLINE_TTL)
    if float((cooldown or {}).get("until") or 0) > time.time():
        stale, _stale_meta = CACHE.get(cache_key, 0, SECTOR_STALE_TTL)
        if isinstance(stale, list):
            return stale
        raise FinanceError("PROVIDER_COOLDOWN", "East Money board history skipped during cooldown", "eastmoney")
    url = f"{_HIS_URL}/stock/kline/get?secid={identifier}&klt=101&fqt=1&lmt={limit}&end=20500101&fields1=f1,f2,f3&fields2=f51,f53"
    try:
        payload = _fetch_json(url)
    except Exception:
        CACHE.set(_KLINE_BREAKER_KEY, {"until": time.time() + SECTOR_KLINE_TTL})
        stale, _stale_meta = CACHE.get(cache_key, 0, SECTOR_STALE_TTL)
        if isinstance(stale, list):
            return stale
        raise
    rows = ((payload.get("data") or {}).get("klines")) or []
    out = []
    for line in rows:
        parts = str(line).split(",")
        try:
            out.append({"date": parts[0], "close": float(parts[1])})
        except (ValueError, IndexError):
            continue
    if out:
        out.sort(key=lambda row: row["date"])
        CACHE.set(cache_key, out)
        return out
    stale, _stale_meta = CACHE.get(cache_key, 0, SECTOR_STALE_TTL)
    return stale if isinstance(stale, list) else []


def cached_kline(identifier: str, limit: int = SECTOR_KLINE_LIMIT, max_age: int = SECTOR_KLINE_TTL, stale_age: int = SECTOR_STALE_TTL) -> list[dict] | None:
    """Read board/index history without doing network I/O."""
    limit = max(2, min(int(limit), 1500))
    cached, _meta = CACHE.get(f"kline:{identifier}:{limit}", max_age, stale_age)
    return cached if isinstance(cached, list) and cached else None


def _enrich_series(rows: list[dict]) -> list[dict]:
    out = []
    for index, row in enumerate(rows):
        prev = rows[index - 1]["close"] if index else row["close"]
        window = rows[max(0, index - 19):index + 1]
        out.append({
            "date": row["date"],
            "close": row["close"],
            "return_1": row["close"] / prev - 1 if prev else 0.0,
            "ma20_gap": row["close"] / (sum(w["close"] for w in window) / len(window)) - 1 if window else 0.0,
            "momentum_20": row["close"] / rows[index - 20]["close"] - 1 if index >= 20 and rows[index - 20]["close"] else 0.0,
        })
    return out


def refresh_flow_today(market: str, symbol: str) -> list[dict] | None:
    """In-session flow refresh: one push2delay call returns today's running
    row only. Merged over the cached history so the interactive path never
    needs the rate-limited push2his host during trading hours."""
    identifier = secid(market, symbol)
    if not identifier:
        return None
    url = f"{_DELAY_URL}/stock/fflow/daykline/get?lmt=1&klt=101&secid={identifier}&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56"
    try:
        rows = _parse_rows(_fetch_json(url))
    except (FinanceError, Exception):
        return None
    if not rows:
        return None
    history, _meta = CACHE.get(f"flow:{market}:{symbol}", 86_400) or (None, {})
    if isinstance(history, list) and history:
        merged = [row for row in history if row.get("date") != rows[0]["date"]] + rows
        merged.sort(key=lambda row: row["date"])
    else:
        merged = rows
    CACHE.set(f"flow:{market}:{symbol}", merged)
    return merged


def sector_identity(market: str, symbol: str) -> dict | None:
    """Resolve an A-share to its East Money industry board for seven days."""
    if market != "cn":
        return None
    cache_key = f"sectorid:{market}:{symbol}"
    cached, _meta = CACHE.get(cache_key, INDUSTRY_TTL)
    if isinstance(cached, dict):
        return cached
    industry = stock_industry(market, symbol)
    if not industry:
        identity = {"industry": None, "board_code": None, "board_name": None}
        CACHE.set(cache_key, identity)
        return identity
    try:
        directory = board_directory()
        board = next((row for row in directory if row.get("name") == industry), None)
    except Exception:
        stale, _stale_meta = CACHE.get(cache_key, 0, SECTOR_STALE_TTL)
        if isinstance(stale, dict):
            return stale
        raise
    identity = {"industry": industry, "board_code": (board or {}).get("code"), "board_name": (board or {}).get("name") or industry}
    # An empty directory during provider degradation must not freeze a missing
    # board mapping for seven days. Cache only a resolved BK identifier.
    if identity.get("board_code"):
        CACHE.set(cache_key, identity)
    return identity


def cached_sector_context(market: str, symbol: str, max_age: int = SECTOR_KLINE_TTL) -> dict | None:
    """Read optional A-share sector context without slowing an analysis."""
    if market != "cn":
        return None
    cached, meta = CACHE.get(f"sectorctx:{symbol}", max_age)
    return {**cached, "cache": meta} if isinstance(cached, dict) and cached.get("industry") else None


def cached_market_reference(board_code: str | None, max_age: int = SECTOR_KLINE_TTL) -> tuple[list[dict], list[dict]] | None:
    """Return pre-warmed causal board/index series without network I/O."""
    if not board_code:
        return None
    board_rows = cached_kline(f"90.{board_code}", SECTOR_KLINE_LIMIT, max_age)
    index_rows = cached_kline("1.000300", SECTOR_KLINE_LIMIT, max_age)
    if not board_rows and not index_rows:
        return None
    return _enrich_series(board_rows or []), _enrich_series(index_rows or [])



def _f10_report(report: str, secucode: str, sort_column: str = "REPORT_DATE") -> list[dict]:
    cache_key = f"f10:{report}:{secucode}"
    cached, _meta = CACHE.get(cache_key, 86_400)
    if cached is not None and isinstance(cached, list):
        return cached
    url = f"https://datacenter.eastmoney.com/securities/api/data/v1/get?reportName={report}&columns=ALL&filter=(SECUCODE%3D%22{secucode}%22)&pageSize=8&sortColumns={sort_column}&sortTypes=-1&source=HSF10&client=PC"
    rows = (( _fetch_json(url).get("result") or {}).get("data")) or []
    if rows:
        CACHE.set(cache_key, rows)
    return rows


def _financial_findings(secucode: str) -> dict:
    findings = {}
    try:
        main = _f10_report("RPT_F10_FINANCE_MAINFINADATA", secucode)
    except Exception:
        main = []
    balance = []
    try:
        balance = _f10_report("RPT_F10_FINANCE_GBALANCE", secucode)
    except Exception:
        pass
    if balance:
        latest = balance[0]
        total_assets = float(latest.get("TOTAL_ASSETS") or 0)
        liabilities = float(latest.get("TOTAL_LIABILITIES") or 0)
        if total_assets > 0:
            ratio = liabilities / total_assets
            findings["financial_distress"] = {
                "detected": ratio >= 1.0,
                "level": "high" if ratio >= 1.0 else "medium",
                "detail": f"资产负债率 {ratio * 100:.1f}%（{str(latest.get('REPORT_DATE'))[:10]} 报告期）",
            }
            findings["financial_analysis"] = {"detected": False, "detail": f"最新报告期 {str(latest.get('REPORT_DATE'))[:10]} 结构化财务数据已接入"}
            goodwill = float(latest.get("GOODWILL") or 0)
            findings["goodwill"] = {
                "detected": total_assets > 0 and goodwill / total_assets >= 0.15,
                "level": "high" if total_assets > 0 and goodwill / total_assets >= 0.30 else "medium",
                "detail": f"商誉占总资产比 {goodwill / total_assets * 100:.2f}%" if goodwill else "账面无商誉",
            }
            inventory_yoy = float(latest.get("INVENTORY_YOY") or 0)
            inventory_ratio = float(latest.get("INVENTORY") or 0) / total_assets
            findings["inventory_impairment"] = {
                "detected": inventory_yoy >= 60.0 and inventory_ratio >= 0.10,
                "level": "medium",
                "detail": f"存货同比 {inventory_yoy:+.1f}%，占总资产 {inventory_ratio * 100:.2f}%",
            }
            receivable_yoy = float(latest.get("ACCOUNTS_RECE_YOY") or 0)
            receivable_ratio = float(latest.get("ACCOUNTS_RECE") or 0) / total_assets
            findings["receivables_bad_debt"] = {
                "detected": receivable_yoy >= 60.0 and receivable_ratio >= 0.15,
                "level": "medium",
                "detail": f"应收账款同比 {receivable_yoy:+.1f}%，占总资产 {receivable_ratio * 100:.2f}%",
            }
            monetary_ratio = float(latest.get("MONETARYFUNDS") or 0) / total_assets
            interest_debt = float(latest.get("INTEREST_DEBT_RATIO") or 0)
            findings["deposit_loan_high"] = {
                "detected": monetary_ratio >= 0.30 and interest_debt >= 20.0,
                "level": "medium",
                "detail": f"货币资金占总资产 {monetary_ratio * 100:.1f}%，带息负债率 {interest_debt:.1f}%",
            }
    if main:
        latest_cash = float(main[0].get("NETCASH_OPERATE_PK") or 0)
        prev_cash = float(main[1].get("NETCASH_OPERATE_PK") or 0) if len(main) > 1 else None
        profit = float(main[0].get("PARENTNETPROFIT") or 0)
        divergence = latest_cash < 0 and profit > 0
        streak = latest_cash < 0 and prev_cash is not None and prev_cash < 0
        findings["cashflow_interruption"] = {
            "detected": streak or divergence,
            "detail": ("经营现金流连续两期为负" if streak else "净利润为正但经营现金流为负") if (streak or divergence) else f"经营现金流为正（净现比 {float(main[0].get('NCO_NETPROFIT') or 0):.2f}）",
        }
        profit_yoy = main[0].get("PARENTNETPROFITTZ")
        profit_yoy = main[0].get("PARENTNETPROFITTZ")
        if profit_yoy is not None and float(profit_yoy) <= -30.0 and "earnings_risk" not in findings:
            findings["earnings_risk"] = {"detected": True, "level": "high" if float(profit_yoy) <= -50.0 else "medium", "detail": f"归母净利润同比 {float(profit_yoy):+.1f}%（最新报告期）"}
    return findings


def _executive_findings(market: str, symbol: str, secucode: str, entity: str | None = None) -> dict:
    findings = {}
    try:
        rows = _f10_report("RPT_EXECUTIVE_HOLD_CHANGE", secucode, "CHANGE_DATE")
    except Exception:
        rows = []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=180)).date()
    reductions = []
    for row in rows or []:
        try:
            day = datetime.strptime(str(row.get("CHANGE_DATE"))[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        if day < cutoff:
            continue
        change_num = float(row.get("CHANGE_NUM") or 0)
        reason = str(row.get("CHANGE_REASON") or "")
        if change_num < 0 or "减持" in reason:
            reductions.append((day, row))
    if reductions:
        reductions.sort(key=lambda item: item[0], reverse=True)
        holder = str(reductions[0][1].get("HOLDER_NAME") or reductions[0][1].get("EXECUTIVE_NAME") or "董监高")
        findings["shareholder_reduction"] = {
            "detected": True,
            "detail": f"近 180 天 {len(reductions)} 笔董监高减持记录（最近 {str(reductions[0][1].get('CHANGE_DATE'))[:10]}，{holder}）",
        }
    elif rows:
        findings["shareholder_reduction"] = {"detected": False, "detail": "近 180 天无董监高减持记录"}
    if entity:
        # Controlling and 5%+ shareholder plans are announced through cninfo;
        # the executive-change report cannot see that tier.
        try:
            announcements = cninfo_reduction_announcements(symbol, entity)
        except Exception:
            announcements = []
        if announcements:
            latest = announcements[0]
            detected = any(term in latest["title"] for term in ("减持", "司法拍卖"))
            # The announcement itself is the finding; appending the executive
            # ledger line after it read as a self-contradiction.
            findings["shareholder_reduction"] = {
                "detected": detected,
                "level": "medium",
                "detail": f"减持相关公告 {len(announcements)} 条，最新：{latest['title']}（{latest['date']}）",
            }
        try:
            findings.update(cninfo_regulatory_findings(symbol, entity))
        except Exception:
            pass
    return findings


_REGULATORY_KEYWORDS = {
    "investigation": ("立案", "侦查"),
    "violation_penalty": ("处罚", "罚款", "警示函", "监管函", "公开谴责"),
    "regulatory_inquiry": ("问询函", "关注函", "监管工作函"),
}
_REGULATORY_LEVEL = {"investigation": "high", "violation_penalty": "high", "regulatory_inquiry": "medium"}


def cninfo_reduction_announcements(symbol: str, keyword: str, days: int = 180) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    return [
        row for row in cninfo_search(symbol, "减持", days=days)
        if row["date"] >= cutoff and any(term in row["title"] for term in ("减持", "司法拍卖"))
    ]


def cninfo_regulatory_findings(symbol: str, keyword: str) -> dict:
    """Investigation / penalty / inquiry verdicts from official filings.

    A single pass over the announcement list fans every title out to the
    three regulator categories, so one cninfo request covers all three.
    """
    announcements = cninfo_search(symbol, "", days=365)
    if not announcements:
        announcements = [row for name in ("立案", "处罚", "问询函") for row in cninfo_search(symbol, name, days=365)]
    announcements = [row for row in announcements if row["date"] >= since]
    findings = {}
    for key, terms in _REGULATORY_KEYWORDS.items():
        for item in announcements or []:
            title = str(item.get("announcementTitle") or "").replace("<em>", "").replace("</em>", "")
            # Only the company's own regulatory filings count; routine titles
            # that merely quote a rule are skipped by the term-in-title check.
            stamp = item.get("announcementTime")
            date = datetime.fromtimestamp(stamp / 1000, tz=timezone.utc).date().isoformat() if stamp else None
            if any(term in title for term in terms):
                findings[key] = {
                    "detected": True,
                    "level": _REGULATORY_LEVEL[key],
                    "detail": f"监管公告：{title[:60]}（{date}）",
                }
                break
        if key not in findings:
            findings[key] = {"detected": False, "detail": f"近一年无{({'investigation': '立案', 'violation_penalty': '处罚', 'regulatory_inquiry': '问询'})[key]}类公告"}
    return findings


def sector_context(market: str, symbol: str) -> dict | None:
    """Industry board plus broad-market context for an A-share, fully cached.

    Returns None when the symbol has no EM industry classification (HK/US etc).
    """
    if market != "cn":
        return None
    cache_key = f"sectorctx:{symbol}"
    cached, meta = CACHE.get(cache_key, SECTOR_CONTEXT_TTL)
    if cached is not None and isinstance(cached, dict):
        return {**cached, "cache": meta}
    identity = sector_identity(market, symbol) or {}
    industry = identity.get("industry")
    if not industry:
        empty = {"industry": None, "board_code": None, "board_name": None, "source": "East Money", "as_of": utc_now()}
        CACHE.set(cache_key, empty)
        return {**empty, "cache": {"cached": False, "stale": False, "age_seconds": 0}}
    directory = board_directory()
    board = next((row for row in directory if row.get("code") == identity.get("board_code")), None)
    if board is None:
        board = next((row for row in directory if row.get("name") == industry), None)
    context = {**identity, "source": "East Money", "as_of": utc_now()}
    if board:
        # clist heat is on push2delay (rarely blocked); klines are on push2his
        # (rate-limited). A blocked kline host must not discard the heat data.
        context.update({
            "board_change_pct_now": board.get("change_pct"),
            "board_main_net": board.get("main_net"),
        })
        try:
            rows = _enrich_series(kline(f"90.{board['code']}", SECTOR_KLINE_LIMIT))
            latest = rows[-1] if rows else {}
            context.update({"board_return_1": latest.get("return_1"), "board_ma20_gap": latest.get("ma20_gap"), "board_momentum_20": latest.get("momentum_20")})
        except Exception:
            context["klines_blocked"] = True
    try:
        index_rows = _enrich_series(kline("1.000300", SECTOR_KLINE_LIMIT))
        index_latest = index_rows[-1] if index_rows else {}
        context.update({"index_return_1": index_latest.get("return_1"), "index_ma20_gap": index_latest.get("ma20_gap"), "index_momentum_20": index_latest.get("momentum_20")})
    except Exception:
        context["klines_blocked"] = True
    context["top_boards"] = directory[:6]
    CACHE.set(cache_key, context)
    return {**context, "cache": {"cached": False, "stale": False, "age_seconds": 0}}


_DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_BAD_FORECAST_TYPES = ("预亏", "首亏", "续亏", "大幅下降", "略降", "增亏")
_GOOD_FORECAST_TYPES = ("预增", "略增", "扭亏", "续盈", "大幅上升")


def _datacenter_report(report: str, secucode: str, sort_column: str | None = None) -> list[dict]:
    cache_key = f"dc:{report}:{secucode}"
    cached, _meta = CACHE.get(cache_key, 86_400)
    if cached is not None and isinstance(cached, list):
        return cached
    url = f"{_DATACENTER_URL}?reportName={report}&columns=ALL&filter=(SECUCODE%3D%22{secucode}%22)&pageSize=8&source=WEB&client=WEB"
    if sort_column:
        url += f"&sortColumns={sort_column}&sortTypes=-1"
    payload = _fetch_json(url)
    rows = ((payload.get("result") or {}).get("data")) or []
    if rows:
        CACHE.set(cache_key, rows)
    return rows


def risk_reports(market: str, symbol: str, security_name: str | None = None) -> dict | None:
    """Structured A-share risk findings from East Money datacenter reports.

    Covered here: equity pledge ratio, upcoming lockup expiries, earnings
    pre-announcements, and the ST name flag. Returns None for non-A-share
    symbols; individual fetch failures degrade to missing keys.
    """
    if market != "cn":
        return None
    identifier = secid(market, symbol)
    if not identifier:
        return None
    secucode = f"{symbol}.{'SH' if symbol.startswith(('6', '9', '5')) else 'SZ'}"
    cache_key = f"riskreports:{symbol}"
    cached, _meta = CACHE.get(cache_key, 600)
    if cached is not None and isinstance(cached, dict):
        return cached
    findings: dict = {}
    result: dict = {"sources": ["东方财富数据中心"], "findings": findings}
    try:
        pledge_rows = _datacenter_report("RPT_CSDC_LIST", secucode, "TRADE_DATE")
        if pledge_rows:
            latest = pledge_rows[0]
            ratio = latest.get("PLEDGE_RATIO")
            if ratio is not None:
                findings["equity_pledge"] = {
                    "detected": float(ratio) >= 30.0,
                    "detail": f"质押比例 {float(ratio):.2f}%（中登 {str(latest.get('TRADE_DATE'))[:10]}）",
                }
    except Exception:
        pass
    try:
        lift_rows = _datacenter_report("RPT_LIFT_STAGE", secucode, "FREE_DATE")
        upcoming = next((row for row in lift_rows if str(row.get("FREE_DATE", "9"))[:10] >= datetime.now(timezone.utc).date().isoformat()), None)
        if upcoming and float(upcoming.get("FREE_RATIO") or 0) >= 0.01:
            findings["lockup_expiry"] = {
                "detected": False,
                "detail": f"下次解禁 {str(upcoming.get('FREE_DATE'))[:10]}，占 {float(upcoming['FREE_RATIO']) * 100:.2f}%",
            }
    except Exception:
        pass
    try:
        forecast_rows = _datacenter_report("RPT_PUBLIC_OP_NEWPREDICT", secucode, "NOTICE_DATE")
        forecast_rows = [
            row for row in forecast_rows
            if (datetime.now(timezone.utc).date() - datetime.strptime(str(row.get("NOTICE_DATE"))[:10], "%Y-%m-%d").date()).days <= 400
        ]
        if forecast_rows:
            latest = forecast_rows[0]
            predict_type = str(latest.get("PREDICT_TYPE") or "")
            report_date = str(latest.get("REPORT_DATE"))[:10]
            if any(bad in predict_type for bad in _BAD_FORECAST_TYPES):
                level = "high" if any(bad in predict_type for bad in ("预亏", "首亏", "续亏")) else "medium"
                findings["earnings_risk"] = {"detected": True, "level": level, "detail": f"业绩预告 {predict_type}（{report_date} 报告期）"}
            elif any(good in predict_type for good in _GOOD_FORECAST_TYPES):
                findings["earnings_risk"] = {"detected": False, "detail": f"业绩预告 {predict_type}（{report_date} 报告期）"}
    except Exception:
        pass
    findings.update(_financial_findings(secucode))
    findings.update(_executive_findings(market, symbol, secucode, entity=security_name))
    name = (security_name or "").strip()
    if name:
        # The exchange name alone settles this category in both directions,
        # so a clean name yields a covered verdict rather than source-limited.
        flagged = "ST" in name.upper()
        findings["st_risk"] = {
            "detected": flagged,
            "detail": f"证券简称含风险警示标记（{name}）" if flagged else f"证券简称无风险警示标记（{name}）",
        }
    CACHE.set(cache_key, result)
    return result


def cached_risk_reports(market: str, symbol: str, security_name: str | None = None, max_age: int = 86_400) -> dict | None:
    """Read-only variant for the interactive analysis path."""
    identifier = secid(market, symbol)
    if not identifier or market != "cn":
        return None
    cached, _meta = CACHE.get(f"riskreports:{symbol}", max_age)
    return cached if isinstance(cached, dict) else None


_CNINFO_SEARCH_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
_CNINFO_ORG_URL = "http://www.cninfo.com.cn/new/information/topSearch/query"
_CNINFO_HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36", "Referer": "http://www.cninfo.com.cn/"}


def cninfo_org_id(keyword: str) -> str | None:
    cache_key = f"cninfo:org:{keyword}"
    cached, _meta = CACHE.get(cache_key, 604_800)
    if cached:
        return cached
    payload, _meta = request_post_json(_CNINFO_ORG_URL, {"keyWord": keyword, "maxNum": "10"}, headers=_CNINFO_HEADERS, timeout=10)
    match = next((item for item in payload if item.get("code") == keyword), None) if isinstance(payload, list) else None
    org = (match or {}).get("orgId")
    if org:
        CACHE.set(cache_key, org)
    return org


def cninfo_search(symbol: str, keyword: str, days: int = 365) -> list[dict]:
    """Recent official cninfo announcements matching a keyword for this stock.

    cninfo's full-text query only honours the stock filter when a searchkey
    is present - without one it silently returns site-wide announcements -
    so every category runs its own keyword search with an independent cache.
    """
    cache_key = f"cninfo:ann:{symbol}:{keyword}"
    cached, _meta = CACHE.get(cache_key, 600)
    if cached is not None and isinstance(cached, list):
        return cached
    org = cninfo_org_id(keyword) or ""
    today = datetime.now(timezone.utc).date().isoformat()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    payload, _meta = request_post_json(
        _CNINFO_SEARCH_URL,
        {
            "pageNum": "1", "pageSize": "15", "column": "sse", "tabName": "fulltext",
            "stock": f"{symbol},{org}", "searchkey": keyword,
            "seDate": f"{since}~{today}", "sortName": "time", "sortType": "desc", "isHLtitle": "true",
        },
        headers=_CNINFO_HEADERS, timeout=12,
    )
    announcements = payload.get("announcements") if isinstance(payload, dict) else None
    result = []
    for item in announcements or []:
        title = str(item.get("announcementTitle") or "").replace("<em>", "").replace("</em>", "").strip()
        stamp = item.get("announcementTime")
        date = datetime.fromtimestamp(stamp / 1000, tz=timezone.utc).date().isoformat() if stamp else None
        if title and date:
            result.append({"title": title, "date": date, "url": f"http://static.cninfo.com.cn/{item.get('adjunctUrl', '')}"})
    return result
