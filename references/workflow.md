# Authoritative finance workflow

This is the single source of truth for CLI, Hermes, web, Telegram, Weixin, and future channels.

```text
User / Web client
      ↓
Platform adapter or same-origin HTTP API
      ↓
Hermes Main (channels only) / validated API request
      ↓
Finance intent and normalized asset
      ↓
Python deterministic engine
      ├─ provider + cache/stale policy
      ├─ quote / normalized history
      ├─ indicators / risk / position math
      ├─ anomaly + volume + range + related-content sentiment
      └─ market-adaptive ensemble path ML + matured-result feedback
      ↓
Structured JSON with source, timestamp, delay and timing
      ↓
Routing gate
      ├─ L0 quote → caller
      ├─ L1 deterministic/ML analysis → caller
      └─ L2 explicit synthesis → optional specialist → caller
```

## Routing levels

- **L0 fact**: current price, change, volume, or stablecoin price. It never calls an LLM.
- **L1 deterministic**: indicators, trend, stablecoin risk, position P&L, news evidence, sentiment, monitoring, and machine learning. It never calls an LLM.
- **L2 synthesis**: explicit comparison, trade-off, scenario, deep analysis, or decision support. Python runs first; only then may the optional specialist interpret the compact verified result.

Material signal conflict is a routing hint, not automatic permission to call a model. Causal questions require news/event evidence; price action alone cannot establish a cause.

## Machine-learning feedback

The model predicts future log returns using only bars available at the prediction origin. Samples are split chronologically and recent samples receive more weight. Market-specific horizons, an adaptive three-component ensemble, expanded hidden training context, and causal amplitude shrinkage reduce phase and overshoot errors without shifting a prediction after the fact. The chart keeps `actual`, historical backtest, and timestamped forward series separate. On `1d/5m`, direct-horizon estimates form a path to the session close and skip exchange breaks. When a calibration target matures, the realized error and direction are recorded once, component errors and return amplitude are recalibrated, and the coefficients receive one incremental update. Repeated requests do not train twice on the same target timestamp.

Confidence is capped and derived from chronological holdout error, directional accuracy, and matured sample count. Its schema marks `not_probability: true`. It is never a trading signal or guarantee.

## Failures

Fresh provider failure may use cache only inside the documented stale window and must set `stale=true`. Older data is rejected. Specialist failure never removes deterministic results. Free-model failure never triggers an unapproved paid model.

The Finance Skill never sends channel messages or places trades. The channel/Main layer remains the final speaker.
