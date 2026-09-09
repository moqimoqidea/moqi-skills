# JSON output

Prices are strings. A comparison contains `query`, `match_mode`, `retrieved_at`, `results`, and `source_checks`.

Each result contains:

- `provider`, `model_id`, `display_name`, `model_family`
- `delivery_mode`, `region`, `currency`
- `offers[]`, each with an independent `name`, `conditions`, and `prices[]`
- `source.url`, `source.kind`, and `source.retrieved_at`

Price fields are `type`, `label`, `amount`, `unit`, and optional `display`, `list_amount`, or `discount`. Common units are `CNY_per_million_tokens`, `USD_per_million_tokens`, their `_per_hour` storage variants, `CNY_per_10k_characters`, and `CNY_per_request`.

`delivery_mode` is `self_deployed`, `platform_hosted`, `third_party_hosted`, `upstream_direct`, or `first_party`. Source status is `available`, `not_found`, or `source_error`; the last means availability and price are unknown.

Cached operations add:

```json
{"cache": {"status": "hit|miss|refreshed|refresh_failed", "fetched_at": "ISO-8601"}}
```
