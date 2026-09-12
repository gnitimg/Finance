# CLI usage

All commands emit one JSON object. Add `--pretty` for readable output.

```bash
finance-skill health --pretty
finance-skill quote --market cn --symbol 601619 --pretty
finance-skill quote --market hk --symbol 00700 --pretty
finance-skill quote --market us --symbol NVDA --pretty
finance-skill analyze --market us --symbol NVDA --range 3mo --interval 1d --pretty
finance-skill analyze --market cn --symbol 601619 --position-shares 1700 --position-cost 5.839 --pretty
finance-skill stablecoin --symbol USDT --pretty
finance-skill news --market us --symbol NVDA --limit 10 --pretty
finance-skill monitor --asset cn:601619 --asset us:NVDA --forecast-pct 0.7 --price-change-pct 2 --volume-ratio 1.8 --pretty
finance-skill workflow --asset hk:00700 --asset hk:09988 --query "深入比较风险收益" --specialist auto --pretty
```

Supported chart pairs are `1d/5m`, `5d/15m`, `5d/30m`, `1mo/1d`, `3mo/1d`, `6mo/1d`, `1y/1d`, and `2y/1d`.

`off` prevents specialist use. `auto` permits it only for an explicit L2 request. `force` is for an already deep L1/L2 request and still runs Python first. Public web deployment sets specialist use to off.

`train` bulk-fits the model for up to twelve repeated `--asset` values and reports the honest per-asset evaluation (skill, directional accuracy, phase, confidence, publishable).

`monitor` accepts at most eight repeated `--asset` values. It returns potential, risk, and anomaly matches plus stable alert IDs. Thresholds are percentages for forward/price movement and a multiple for relative volume; no LLM is called.
