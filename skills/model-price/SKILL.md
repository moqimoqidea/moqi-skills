---
name: model-price
description: Find major AI models and compare current official prices across cloud platforms and first-party providers. Use for model availability, versions, service modes, token or cache pricing, and provider comparisons involving Aliyun, Volcengine, Tencent Cloud, DeepSeek, Kimi, Zhipu, MiniMax, OpenAI, Anthropic, or Google Gemini.
---

# Model Price

Run the bundled script before answering. Show every returned provider, version, service mode, region, price condition, official source, and source status.

```bash
# Search domestic providers. GPT, Claude, or Gemini names also add their relevant first-party provider.
python3 scripts/query_model_prices.py compare MODEL --format markdown

# Query selected providers, require an exact ID, or include all overseas providers.
python3 scripts/query_model_prices.py compare MODEL --provider PROVIDER --provider PROVIDER
python3 scripts/query_model_prices.py compare MODEL --exact
python3 scripts/query_model_prices.py compare MODEL --include-overseas

# Query or list one provider.
python3 scripts/query_model_prices.py provider PROVIDER MODEL --format markdown
python3 scripts/query_model_prices.py list PROVIDER --prefix PREFIX
```

Provider IDs: `aliyun`, `volcengine`, `tencent`, `deepseek`, `kimi`, `zhipu`, `minimax`, `openai`, `anthropic`, `google`.

Do not query `openai`, `anthropic`, or `google` by default. Add only the relevant provider when the user explicitly mentions GPT/OpenAI, Claude/Anthropic, or Gemini/Google; use `--include-overseas` when the user explicitly asks about overseas models generally.

Each provider caches lists and price searches independently under `cache/`. Fresh caches are valid for 3 hours. Add `--refresh` only when the user asks for the latest/current refresh; with `provider` or repeated `--provider`, refresh only those providers. If an overseas source is unreachable and no fresh cache exists, report `source_error` and say its price is unknown.

Use family matching for availability comparisons; use `--exact` only for an exact official ID. Never merge self-deployed, platform-hosted, third-party-hosted, upstream-direct, first-party, Batch, cache, context, service-tier, time-band, promotion, version, currency, or region variants.

Model identity ignores documentation footnote markers (`deepseek-flash(1)` == `deepseek-flash`). Officially retired names are mapped to their live model, so `deepseek-v4-flash` resolves to `deepseek-flash`; always report the `model_id` actually returned. A model that is listed but priced per second, per request, or only on a free tier produces no price rows — say so rather than reporting a price of zero.

Prefer an official structured JSON response used by a documentation page. Fall back to official Markdown or HTML only when no public JSON source exists. Never use credentials or private console data.

Read [references/schema.md](references/schema.md) when consuming JSON. Read [references/source-notes.md](references/source-notes.md) only when a source or parser needs maintenance.
