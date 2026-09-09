# Normalized output schema

The script emits JSON by default. Prices remain strings so decimal values are not rounded by binary floating point.

```json
{
  "query": "glm-5.3",
  "retrieved_at": "2026-09-09T07:42:31+08:00",
  "results": [
    {
      "provider": {"id": "tencent", "name": "腾讯云 TokenHub"},
      "model_id": "glm-5.3",
      "display_name": "GLM-5.3",
      "region": "中国区（广州）",
      "currency": "CNY",
      "offers": [
        {
          "name": "online_standard",
          "conditions": {},
          "prices": [
            {
              "type": "input",
              "label": "推理输入",
              "amount": "8",
              "unit": "CNY_per_million_tokens"
            }
          ]
        }
      ],
      "source": {
        "url": "https://cloud.tencent.com/document/product/1823/130055",
        "kind": "official_document",
        "retrieved_at": "2026-09-09T07:42:33+08:00"
      }
    }
  ],
  "source_checks": [
    {
      "provider": {"id": "aliyun", "name": "阿里云百炼"},
      "status": "not_found",
      "source": {
        "url": "https://bailian-cs.console.aliyun.com/data/api.json?action=BroadScopeAspnGateway&product=sfm_bailian&api=zeldaHttp.dashscopeModel./zelda/api/v1/modelCenter/listFoundationModels&_v=undefined",
        "kind": "anonymous_api",
        "retrieved_at": "2026-09-09T07:42:31+08:00"
      }
    }
  ]
}
```

## Status semantics

- `available`: an exact normalized model ID or an unambiguous official display-name match has price data.
- `not_found`: the source was read successfully but contained no exact match.
- `source_error`: the network request, response shape, or parser failed. Do not interpret this as lack of availability.

## Price types

Common types are `input`, `output`, `cache_hit`, `cache_write`, `cache_storage`, `batch_input`, `batch_output`, and `batch_cache_hit`. Provider-specific items remain present with their original label.

Common units are:

- `CNY_per_million_tokens`
- `CNY_per_million_tokens_per_hour`
- `CNY_per_10k_characters`
- `CNY_per_request`

`list_amount` is the crossed-out or undiscounted price. `amount` is the currently displayed price. `conditions` can include context length, time band, service tier, status, or promotion. Treat each offer as a separate billable case.
