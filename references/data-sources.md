# Data sources and semantics

| Scope | Primary source | Semantics |
|---|---|---|
| A-share snapshot | Sina Finance | Public snapshot, normally near-live; timestamp and session state are authoritative. |
| A-share history | Yahoo Finance | Public chart feed; may be delayed. |
| HK / US | Yahoo Finance | Public chart feed; exchange delay may apply. It is not SIP or direct exchange data. |
| Metals GOLD/SILVER/COPPER quote | Sina international futures snapshot | Near-live public snapshot; headline price and as-of come from it. Bars stay on Yahoo. |
| Metals PLATINUM/PALLADIUM and metal bars | Yahoo Finance | Public futures chart feed, roughly ten minutes delayed; the UI flags it as delayed. |
| Crypto | CoinGecko | Aggregated public feed, Crypto. Known stablecoins use verified IDs; other explicit crypto symbols use an exact-symbol search fallback. |
| Stablecoin pools | DexScreener | Verified chain/token and counter-asset allowlist; liquidity filter applies. |
| Stablecoin supply | DefiLlama | Supply context, never used as a price source. |
| Main-force flow | East Money | Cached causal daily context for listed-market models; refreshed by the background calibration job. |
| A-share industry board + CSI 300 | East Money | Industry identity cached seven days; 1,200 daily rows cached 30 minutes and used only causally by the A-share daily model. |
| A-share order book | Tencent Finance | Current five-level imbalance context; optional and independently degradable. |
| Related content | East Money + GDELT + Yahoo Finance RSS | Concurrent and independently degradable; absence does not imply no event. Routine sentiment and risk classification stay local and deterministic; remote LLM reranking is forbidden on this path. |
| Listed-stock risk evidence | Related-content feeds + East Money main-force flow | Deterministic 19-category keyword/event screen. Every hit retains its source and timestamp. Main-force flow is an explicitly labeled proxy for large-order direction. |
| Financial-statement risk | Related-content mentions only | Partial coverage until structured filings and statements are connected. No headline hit must not be described as a clean bill of health. |

On a manual selection the web interface renders a deterministic quote/history snapshot first, then replaces it with the full calibrated model, and only afterward refreshes optional related content and A-share sector context. For listed-market `1D/5m`, one five-day upstream request supplies both the hidden training context and an exchange-local latest-session display slice. This keeps duplicate and optional sources off the critical path. Web analysis never waits on East Money board history; it consumes only pre-warmed caches. The selected quote refreshes every 3 seconds, model analysis every 10 seconds, and watchlist scan every 8 seconds. News is cached separately, while a throttled serial queue refreshes risk context for browser-monitored listed stocks without blocking the monitor response. Price/volume/range sentiment is recomputed against current market context. This is continuous near-live updating, not a promise of exchange-colocated low latency. Every response contains `source`, `feed`, `as_of`, and cache fields.
