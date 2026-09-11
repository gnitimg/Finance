from __future__ import annotations

import re

STABLECOINS = {
    "USDT", "USDC", "DAI", "USDE", "FDUSD", "TUSD", "PYUSD", "USDD",
    "FRAX", "GHO", "LUSD", "CRVUSD", "USDS", "USDP", "GUSD", "SUSD",
    "USD1", "RLUSD", "USDG",
}
ETF_SYMBOLS = {
    "SPY", "QQQ", "DIA", "IWM", "VTI", "VOO", "ARKK", "TLT", "HYG",
    "GLD", "SLV", "USO", "UNG", "EEM", "FXI", "KWEB",
}
FUND_SYMBOLS = {"VTSAX", "FXAIX", "SWPPX", "FZROX", "VFIAX", "VTIAX", "PRNHX"}
METAL_SYMBOLS = {"GOLD", "SILVER", "PLATINUM", "PALLADIUM", "COPPER"}
METAL_YAHOO = {
    "GOLD": "GC=F", "SILVER": "SI=F", "PLATINUM": "PL=F",
    "PALLADIUM": "PA=F", "COPPER": "HG=F",
}
ALIASES = {
    "TENCENT": ("hk", "00700"),
    "腾讯": ("hk", "00700"),
    "腾讯控股": ("hk", "00700"),
    "阿里": ("hk", "09988"),
    "阿里巴巴": ("hk", "09988"),
    "英伟达": ("us", "NVDA"),
    "苹果": ("us", "AAPL"),
    "宁德时代": ("cn", "300750"),
    "贵州茅台": ("cn", "600519"),
    "茅台": ("cn", "600519"),
    "黄金": ("metal", "GOLD"),
    "金价": ("metal", "GOLD"),
    "白银": ("metal", "SILVER"),
    "银价": ("metal", "SILVER"),
    "标普ETF": ("etf", "SPY"),
    "纳指ETF": ("etf", "QQQ"),
}


def normalize(market: str, symbol: str) -> tuple[str, str]:
    raw = symbol.strip()
    alias = ALIASES.get(raw) or ALIASES.get(raw.upper())
    if alias:
        return alias
    value = raw.upper().replace(" ", "")
    requested = market.lower().strip()
    if requested not in {"auto", "cn", "hk", "us", "crypto", "etf", "fund", "future", "metal"}:
        raise ValueError("unsupported market or product type")
    if value.endswith((".SS", ".SZ")):
        requested, value = "cn", value.split(".")[0]
    elif value.endswith(".HK"):
        requested, value = "hk", value.split(".")[0].zfill(5)
    elif value in STABLECOINS:
        requested = "crypto"
    elif requested == "auto":
        if value in METAL_SYMBOLS:
            requested = "metal"
        elif value in ETF_SYMBOLS:
            requested = "etf"
        elif value in FUND_SYMBOLS:
            requested = "fund"
        elif re.fullmatch(r"[A-Z0-9.^-]{1,12}=F", value):
            requested = "future"
        elif re.fullmatch(r"\d{6}", value):
            requested = "cn"
        elif re.fullmatch(r"\d{4,5}", value):
            requested, value = "hk", value.zfill(5)
        elif re.fullmatch(r"[A-Z][A-Z0-9.\-^]{0,14}", value):
            requested = "us"
        else:
            raise ValueError("unable to infer market from symbol")
    if requested == "cn" and not re.fullmatch(r"\d{6}", value):
        raise ValueError("CN symbol must contain 6 digits")
    if requested == "hk":
        value = value.zfill(5)
        if not re.fullmatch(r"\d{5}", value):
            raise ValueError("HK symbol must contain 5 digits")
    if requested == "us" and not re.fullmatch(r"[A-Z^][A-Z0-9.\-^]{0,14}", value):
        raise ValueError("invalid US symbol")
    if requested == "crypto" and value not in STABLECOINS and not re.fullmatch(r"[A-Z][A-Z0-9]{1,11}", value):
        raise ValueError("invalid crypto symbol")
    if requested in {"etf", "fund"} and not re.fullmatch(r"[A-Z0-9.^-]{1,16}", value):
        raise ValueError("invalid fund or ETF symbol")
    if requested == "future" and not re.fullmatch(r"[A-Z0-9.^-]{1,12}=F", value):
        raise ValueError("futures symbols must use the Yahoo form, for example ES=F")
    if requested == "metal" and value not in METAL_SYMBOLS:
        raise ValueError("supported metals are GOLD, SILVER, PLATINUM, PALLADIUM, and COPPER")
    return requested, value


def yahoo_symbol(market: str, symbol: str) -> str:
    if market == "hk":
        return f"{symbol[1:] if len(symbol) == 5 and symbol.startswith('0') else symbol}.HK"
    if market == "cn":
        suffix = ".SS" if symbol.startswith(("5", "6", "9")) else ".SZ"
        return symbol + suffix
    if market == "metal":
        return METAL_YAHOO[symbol]
    return symbol
