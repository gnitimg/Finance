# Finance

A Hermes-compatible finance Skill plus a Vue real-time research interface. Python owns market data, indicators, risk, news sentiment, position math, monitoring, and online model calibration. LLM use is optional and restricted to explicit L2 synthesis.

The chart uses real exchange timestamps and an interactive zoom strip. Intraday `1D` analysis builds a product-specific four-component ensemble path to the current session close and recalibrates as new bars mature. Four causal support/pressure overlays distinguish ultra-short and short windows and retain their price-bar source, time, distance, and method. The v14 A-share daily model adds causal industry-board and CSI 300 context, including board momentum and stock-relative-to-board strength. A manual asset change renders a lightweight snapshot first, loads the model next, then refreshes optional related content and sector caches so slow providers do not block the visible page. The browser UI includes a settings center for category-based watchlists, browser-local time-zone choice, source health, deterministic potential/risk/anomaly monitoring, persistent user-dismissed alerts, and notification history. Its listed-stock risk console monitors 19 public-event, governance, flow, and financial-warning categories; evidence gaps remain explicitly visible instead of being labeled safe. Its clock uses the browser's IANA time zone by default while calibrating the instant against the server. Stablecoin auto-detection covers the configured catalog, with exact-symbol CoinGecko discovery available for explicit crypto searches; missing chain coverage is reported rather than treated as risk.

## Local setup

```bash
bash setup.sh
npm install
npm run check
.venv/bin/python scripts/finance.py health --pretty
```

Run the production service locally:

```bash
npm run build
PORT=8790 npm start
```

The Node process listens only on `127.0.0.1` and serves the built Vue app plus same-origin `/api` routes. Nginx terminates TLS and proxies to it.

## Hermes installation

Copy this project without `.env`, caches, model state, `node_modules`, or `web/dist` into `~/.hermes/skills/finance`, then run:

```bash
cd ~/.hermes/skills/finance
bash setup.sh
install -Dm755 tools/finance_skill_launcher.py ~/.local/bin/finance-skill
finance-skill health --pretty
hermes skills list
```

Secrets belong in `~/.hermes/.env` or the service-specific `.env`, never in JSON, source files, logs, or the browser. The keyless providers are sufficient for the default site.

Deployment templates are in `deploy/`. The public hostname requires a DNS record and its own certificate for `finance.gnitimg.ac.cn`.
