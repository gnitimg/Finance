from __future__ import annotations


def analyze_position(price: float, shares: float | None, cost_basis: float | None) -> dict | None:
    if shares is None and cost_basis is None:
        return None
    if shares is None or cost_basis is None or shares <= 0 or cost_basis <= 0:
        raise ValueError("position shares and cost basis must both be positive")
    total_cost = shares * cost_basis
    market_value = shares * price
    pnl = market_value - total_cost
    pnl_pct = pnl / total_cost * 100
    required_gain_pct = (cost_basis / price - 1) * 100 if price else None
    return {
        "shares": shares,
        "cost_basis": cost_basis,
        "total_cost": total_cost,
        "market_value": market_value,
        "unrealized_pnl": pnl,
        "unrealized_pnl_pct": pnl_pct,
        "break_even_price_ex_fees": cost_basis,
        "required_gain_to_break_even_pct": required_gain_pct,
        "facts": [
            f"Total cost {total_cost:,.2f}",
            f"Current market value {market_value:,.2f}",
            f"Unrealized P&L {pnl:,.2f} ({pnl_pct:.4f}%)",
            f"Break-even price excluding fees {cost_basis:.4f}",
            f"Required move to break even {required_gain_pct:.4f}%" if required_gain_pct is not None else "Required move unavailable",
        ],
    }
