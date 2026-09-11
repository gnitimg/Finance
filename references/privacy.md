# Privacy and specialist boundary

Quotes, charts, indicators, news sentiment, monitoring, model training, forecast settlement, and position math run locally in Python.

The optional L2 specialist receives only compact normalized market facts and the minimal analysis goal. A recursive filter removes user/channel IDs, phone numbers, account identifiers, authorization values, tokens, and common API-key patterns. Raw chat history, screenshots, account numbers, and large K-line arrays are excluded.

Public web requests do not enable the specialist. Enabling it requires explicit server configuration and an allowlisted provider/model. Model failures degrade to deterministic results; they do not authorize a paid fallback.
