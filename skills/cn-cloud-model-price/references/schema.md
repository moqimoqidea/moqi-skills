# JSON schema notes

Prices remain strings to avoid decimal rounding. A comparison contains:

```json
{
  "query": "deepseek-v4-pro",
  "match_mode": "model_family",
  "retrieved_at": "ISO-8601",
  "results": [
    {
      "provider": {"id": "tencent", "name": "腾讯云 TokenHub"},
      "model_id": "deepseek-v4-pro-0813",
      "model_aliases": ["deepseek-v4-pro-0813"],
      "model_family": "deepseek-v4-pro-0813",
      "display_name": "DeepSeek-V4-Pro 0813 正式版",
      "delivery_mode": "self_deployed",
      "region": "中国区（广州）",
      "currency": "CNY",
      "offers": [
        {
          "name": "online_conditional",
          "conditions": {"time_band": "空闲时段"},
          "prices": [
            {
              "type": "input",
              "label": "推理输入",
              "amount": "4.5",
              "unit": "CNY_per_million_tokens"
            }
          ]
        }
      ],
      "source": {"url": "official URL", "kind": "official_document", "retrieved_at": "ISO-8601"}
    }
  ],
  "source_checks": []
}
```

`delivery_mode` is `self_deployed`, `platform_hosted`, `third_party_hosted`, `upstream_direct`, or `first_party`. Each offer is one billable case; never merge its conditions with another offer.

Common price types are `input`, `output`, `audio_input`, `cache_hit`, `audio_cache_hit`, `cache_write`, `cache_storage`, `batch_input`, `batch_output`, and `batch_cache_hit`. Common units are `CNY_per_million_tokens`, `CNY_per_million_tokens_per_hour`, `CNY_per_10k_characters`, and `CNY_per_request`. `list_amount` is an undiscounted price.

Source-check statuses are `available`, `not_found`, and `source_error`. The last means availability is unknown.
