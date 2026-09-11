# GNITIMG Finance

A Hermes-compatible finance Skill plus a Vue real-time research interface. Python owns market data, indicators, risk, news sentiment, position math, monitoring, and online model calibration. LLM use is optional and restricted to explicit L2 synthesis.

The chart uses real exchange timestamps and an interactive zoom strip. Intraday `1D` analysis builds a market-adaptive ensemble path to the current session close and recalibrates as new bars mature. The browser UI includes a settings center for category-based watchlists, browser-local time-zone choice, source health, deterministic potential/risk/anomaly monitoring, persistent user-dismissed alerts, and notification history. Its clock uses the browser's IANA time zone by default while calibrating the instant against the server. Stablecoin auto-detection covers the configured catalog, with exact-symbol CoinGecko discovery available for explicit crypto searches; missing chain coverage is reported rather than treated as risk.

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
