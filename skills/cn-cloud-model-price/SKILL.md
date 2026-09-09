---
name: cn-cloud-model-price
description: Find which China cloud platforms offer a model and compare current official prices across self-deployed, platform-hosted, third-party, and upstream-direct variants. Use for model availability, pricing, cache costs, versions, or provider comparisons involving Aliyun, Volcengine, Tencent Cloud, DeepSeek, Kimi, Zhipu, or MiniMax.
---

# China Cloud Model Price

Run the bundled script before answering; prices and catalogs change frequently.

```bash
# Find a model family across every provider, including versions and service modes.
python3 scripts/query_model_prices.py compare MODEL --format markdown

# Restrict the provider or require one exact official ID/name.
python3 scripts/query_model_prices.py provider PROVIDER MODEL --format markdown
python3 scripts/query_model_prices.py compare MODEL --exact --format markdown

# List a provider's current catalog.
python3 scripts/query_model_prices.py list PROVIDER --prefix PREFIX
```

Provider IDs are `aliyun`, `volcengine`, `tencent`, `deepseek`, `kimi`, `zhipu`, and `minimax`.

Use family matching for questions such as “哪些平台有 deepseek-v4-pro”. Show every returned platform, version, and service mode; do not collapse self-deployed, platform-hosted, third-party-hosted, or upstream-direct offerings into one row. Use `--exact` only when the user explicitly requires an exact invocation ID.

Report current China-region CNY prices by default. Keep Batch, cache, context tiers, service tiers, peak/off-peak bands, promotions, versions, and regions separate. Include the official source and retrieval time. Treat `source_error` as unknown availability, not as “not offered”.

Prefer an official structured JSON endpoint used by a documentation page over parsing rendered HTML or Markdown. Never use login cookies, tokens, copied browser headers, or private console credentials.

Read [references/schema.md](references/schema.md) only when extending or consuming JSON output. Read [references/source-notes.md](references/source-notes.md) only when a source fails or its parser must change.
