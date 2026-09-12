# Data sources and semantics

| Scope | Primary source | Semantics |
|---|---|---|
| A-share snapshot | Sina Finance | Public snapshot, normally near-live; timestamp and session state are authoritative. |
| A-share history | Yahoo Finance | Public chart feed; may be delayed. |
| HK / US | Yahoo Finance | Public chart feed; exchange delay may apply. It is not SIP or direct exchange data. |
| Metals GOLD/SILVER/COPPER quote | Sina international futures snapshot | Near-live public snapshot; headline price and as-of come from it. Bars stay on Yahoo. |
| Metals PLATINUM/PALLADIUM and metal bars | Yahoo Finance | Public futures chart feed, roughly ten minutes delayed; the UI flags it as delayed. |
| Crypto | CoinGecko | Aggregated public feed, 24/7. Known stablecoins use verified IDs; other explicit crypto symbols use an exact-symbol search fallback. |
| Stablecoin pools | DexScreener | Verified chain/token and counter-asset allowlist; liquidity filter applies. |
| Stablecoin supply | DefiLlama | Supply context, never used as a price source. |
| Main-force flow | East Money | Cached causal daily context for listed-market models; refreshed by the background calibration job. |
| A-share order book | Tencent Finance | Current five-level imbalance context; optional and independently degradable. |
| Related content | East Money + GDELT + Yahoo Finance RSS | Concurrent and independently degradable; absence does not imply no event. Routine sentiment is deterministic; an optional relevance reranker may reorder evidence. |

On a manual selection the web interface renders a deterministic quote/history snapshot first, then replaces it with the full calibrated model, and only afterward refreshes optional related content. For listed-market `1D/5m`, one five-day upstream request supplies both the hidden training context and an exchange-local latest-session display slice. This keeps duplicate and optional sources off the critical path. The selected quote refreshes every 3 seconds, model analysis every 10 seconds, and watchlist scan every 8 seconds. News is cached separately, while price/volume/range sentiment is recomputed against current market context. This is continuous near-live updating, not a promise of exchange-colocated low latency. Every response contains `source`, `feed`, `as_of`, and cache fields.
