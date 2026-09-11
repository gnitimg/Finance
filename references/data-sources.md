# Data sources and semantics

| Scope | Primary source | Semantics |
|---|---|---|
| A-share snapshot | Sina Finance | Public snapshot, normally near-live; timestamp and session state are authoritative. |
| A-share history | Yahoo Finance | Public chart feed; may be delayed. |
| HK / US | Yahoo Finance | Public chart feed; exchange delay may apply. It is not SIP or direct exchange data. |
| Crypto | CoinGecko | Aggregated public feed, 24/7. Known stablecoins use verified IDs; other explicit crypto symbols use an exact-symbol search fallback. |
| Stablecoin pools | DexScreener | Verified chain/token and counter-asset allowlist; liquidity filter applies. |
| Stablecoin supply | DefiLlama | Supply context, never used as a price source. |
| News | GDELT + Yahoo Finance RSS | Concurrent and independently degradable; absence does not imply no event. |

The web interface refreshes the selected asset every 15 seconds. News content is cached separately, while price/volume/range sentiment is recomputed against current market context. This is continuous near-live updating, not a promise of exchange-colocated low latency. Every response contains `source`, `feed`, `as_of`, and cache fields.
