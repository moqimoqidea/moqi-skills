---
name: cn-cloud-model-price
description: Query and compare public China-region model prices across Aliyun, Volcengine, Tencent Cloud, DeepSeek, Kimi, Zhipu, and MiniMax, with exact model matching, cache prices, billing conditions, retrieval times, and official source URLs. Use for questions about which vendors offer a model and what each vendor charges.
---

# China Cloud Model Price

Use `scripts/query_model_prices.py` to obtain fresh prices from anonymous APIs or official public documents. Do not answer from the checked-in evidence snapshots when live sources are reachable.

For a cross-provider question, run:

```bash
python3 scripts/query_model_prices.py compare MODEL_ID --format markdown
```

For one provider or a catalog, run:

```bash
python3 scripts/query_model_prices.py provider PROVIDER MODEL_ID --format markdown
python3 scripts/query_model_prices.py list PROVIDER --prefix PREFIX
```

Provider IDs are `aliyun`, `volcengine`, `tencent`, `deepseek`, `kimi`, `zhipu`, and `minimax`.

Apply these rules when presenting results:

- Use China-region CNY online-inference list prices as the default comparison. Preserve foreign currencies if a China price is unavailable.
- Keep cache, Batch, context tiers, service tiers, peak/off-peak bands, and promotions separate. Never merge prices across different conditions or regions.
- Match exact normalized model IDs. Do not treat `glm-5.3-flash` as `glm-5.3`, or silently substitute a dated version.
- Every answer must include the official source URL, source type, retrieval time, region, currency, unit, and applicable conditions so the user can double-check it.
- Distinguish `not_found` from `source_error`. A parser failure is not evidence that a provider lacks the model.
- Never add Cookie, OAuth, API keys, console tokens, CSRF values, account IDs, or copied browser headers. Tencent's console API requires login; use its public documentation source.

Read [references/schema.md](references/schema.md) when consuming JSON output or extending the normalized record. Read [references/source-notes.md](references/source-notes.md) when a provider changes its page format, a query fails, or source-selection details matter.

