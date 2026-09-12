---
name: finance
description: Fetch verified live or delayed market data and perform deterministic stock, ETF, fund, futures, metal, crypto, stablecoin, technical, position-risk, news-sentiment, and monitoring analysis. Use for prices, charts, indicators, comparisons, scenarios, depeg checks, and market screening; do not use it to place trades.
---

# Finance

Use the Python deterministic engine before making any financial claim. Never calculate prices, indicators, position P&L, forecasts, or risk from memory.

## Commands

The installed launcher is `finance-skill`. If unavailable, run `${HERMES_SKILL_DIR}/.venv/bin/python ${HERMES_SKILL_DIR}/scripts/finance.py`.

- Current quote: `finance-skill quote --market us --symbol NVDA`
- ETF / fund / futures / metal: `finance-skill analyze --market etf --symbol SPY`, `--market fund --symbol VTSAX`, `--market future --symbol ES=F`, or `--market metal --symbol GOLD`
- Technical and ML analysis: `finance-skill analyze --market hk --symbol 00700`
- Stablecoin risk: `finance-skill stablecoin --symbol USDE`
- Abnormal-move, volume, range, and related-news sentiment: `finance-skill news --market cn --symbol 601619`
- Watchlist screening and alert candidates: `finance-skill monitor --asset cn:601619 --asset us:NVDA`
- Unified natural-language flow: `finance-skill workflow --asset us:NVDA --query "分析技术面"`
- Position analysis: append `--position-shares 100 --position-cost 120.5` to `analyze`.
- Provider status: `finance-skill health`

Valid markets or product profiles are `auto`, `cn`, `hk`, `us`, `crypto`, `etf`, `fund`, `future`, and `metal`. Prefer `quote` for a simple current-price request. Use `workflow` for comparison, scenario, or deep-analysis requests; repeat `--asset` for multiple assets.

## Operating contract

1. Treat provider timestamps, feed delay, cache, stale flags, warnings, and errors as part of the answer.
2. L0 fact requests never use an LLM. L1 deterministic and ML requests do not use an LLM. Only L2 comparison, scenario, decision-support, deep-analysis, or materially conflicting signals may use the optional specialist.
3. Forecasting is Python-only. Keep predicted and realized series separate. Use product-specific horizons and an inspectable four-component ensemble: regularized return regression, similar historical regimes, short-horizon velocity, and mean reversion. Weight components only from matured walk-forward error and direction results; cap standardized features and component returns. Calibrate amplitude with a causal weighted median that minimizes matured absolute error. The v14 A-share daily profile has 33 causal inputs: 26 price/volume features, two fund-flow features, two CSI 300 features, and three industry-board features (daily return, 20-day momentum, and stock return minus board return). Default charts to the forward path after the latest observed bar; show historical forecasts only when the user enables the complete prediction trail in Settings. Historical visualization shows one-shot walk-forward predictions (each point anchored at its own bar's actual close) so fit is visible; the free-running compounded path stays in the API payload for evaluation and must never be merged with observed prices. A latest-bar contradiction may damp an older model direction but must not replace it with assumed momentum. For `1d/5m`, return a timestamped forward path from the latest observed bar to that market session's close, fit no more than six direct future horizons, interpolate cumulative returns between those anchors, and rebuild on every new bar. Once a calibration horizon matures, feed the realized result into the next training cycle. Forecast confidence is historical calibration quality, never a probability or promise.
4. A specialist interprets only the engine's compact verified payload. It never fetches quotes, calculates indicators, trains models, sees channel/user IDs, or places trades.
5. If providers or the specialist fail, return the usable deterministic result and say what is unavailable. Never invent current data, news, causes, or forecast accuracy.
6. For causal questions without event evidence, state that price/volume data cannot confirm the cause.
7. Market sentiment must combine price anomaly, relative volume, range expansion, technical structure, and available related content. Missing news means missing evidence, not neutral proof.
8. Keep user-facing answers concise: asset, price/state, conclusion, 2–5 signals, risks, as-of time, and source/delay. Include the non-advice disclaimer for decision support.
9. Monitoring is deterministic and bounded to eight assets per request. A model forecast may trigger an alert only after beating the no-change holdout baseline, reaching at least 50% direction accuracy, and passing the phase-lag gate. Failed validation or negative phase makes the forecast non-publishable and damps the visible path to 35% of its calibrated amplitude. Browser watchlists, category choices, time-zone choice, prediction-trail choice, read state, and popup-dismissal state remain local to that browser. A market alert stays visible until the user explicitly closes it and remains available in the notification center afterward.
10. Web analysis must never fetch East Money board history on the asset-switching path; it may use only pre-warmed sector caches. Direct Hermes CLI analysis may warm its own cache on the first uncached A-share request. Cache industry identity for seven days and board/index K-lines for 30 minutes; refresh them sequentially in the background and defer heavy refreshes during the A-share pre-open and trading window.
11. Listed-stock risk screening is deterministic and evidence-first. Track negative publicity, equity pledges, investigations, penalties, regulatory inquiries, negative ratings, lock-up expiry, shareholder selling, material main-force outflow, earnings warnings, audit opinions, financial irregularities, ST/delisting risk, goodwill, high cash-and-debt, cash-flow interruption, inventory impairment, receivables bad debt, and financial distress. Preserve a source and timestamp for every hit. `no_evidence` means only that checked sources did not contain a hit; `source_limited` means structured filings or statements are still required. Never translate either state into “safe”. Watchlist risk news refreshes outside the interactive quote path and is throttled per asset.
12. Technical analysis returns four causal dynamic levels: ultra-short support/pressure and short support/pressure. Compute them only from bars available at the current timestamp using confirmed swing points, EMA anchors, and an ATR14 fallback boundary. Include the interval window, method, source, as-of time, distance from price, and touch count. Treat levels as changing reference zones, not exact execution prices or trade instructions.

Read [references/workflow.md](references/workflow.md) for the only authoritative workflow. Read [references/usage.md](references/usage.md) for command details, [references/data-sources.md](references/data-sources.md) for feed semantics, and [references/privacy.md](references/privacy.md) before enabling a remote specialist.
