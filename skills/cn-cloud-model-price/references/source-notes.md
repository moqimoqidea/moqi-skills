# Source notes

## Source priority

Prefer sources in this order:

1. Anonymous provider price API that returns the live model record.
2. Official public pricing document or official Markdown.
3. Official public page configuration API used to render the pricing page.

Do not use third-party price aggregators when one of these official sources is available.

## Providers

### Aliyun Bailian

- Live anonymous API: `https://bailian-cs.console.aliyun.com/data/api.json?action=BroadScopeAspnGateway&product=sfm_bailian&api=zeldaHttp.dashscopeModel./zelda/api/v1/modelCenter/listFoundationModels&_v=undefined`
- The request needs `Api`, `Data.input`, and `Data.cornerstoneParam`. The cornerstone object may be empty.
- `queryPrice:true` is required for price items. `model` accepts one string, not an array or comma-separated list.
- No Cookie, Authorization, OAuth, `sec_token`, `region`, or `V` field is needed.
- Catalog pages are capped at 50 results and must be paginated.

### Volcengine Ark

- Live anonymous API: `https://arkbff-cn-beijing.console.volcengine.com/api/2024-10-01/GetModelSquareTopData`
- A catalog request uses `{"viewType":"detail","modules":["pricing"]}`.
- A single-model request additionally supplies exactly one `{modelName, modelVersion}` selection.
- POST and JSON content type are required. Login cookies, CSRF, web IDs, Origin, Referer, and `region` are not required.
- A model version is part of the full model ID. Avoid silently choosing among multiple versions.
- Official catalog documentation: `https://docs.volcengine.com/docs/82379/1544106?lang=zh`

### Tencent Cloud TokenHub

- Model catalog: `https://cloud.tencent.com/document/product/1823/130051`
- Model pricing: `https://cloud.tencent.com/document/product/1823/130055`
- Use the Guangzhou tab for the default China-region comparison. Keep Singapore prices separate.
- The public document contains an embedded router state whose `slate` field can be multiply JSON-encoded.
- The console `DescribeModelList` request is not an anonymous source: deleting login cookies causes `UIN_OR_SKEY_MISSING`. Do not copy or ask for console credentials for ordinary price queries.

### DeepSeek

- Official pricing page: `https://api-docs.deepseek.com/zh-cn/quick_start/pricing/`
- Preserve both peak and off-peak rows. Peak is Beijing time Monday-Friday 09:00-12:00 and 14:00-18:00; other times are off-peak.

### Kimi

- Documentation index: `https://platform.kimi.com/docs/llms.txt`
- The index links to model-specific official Markdown documents under `/docs/pricing/chat-*.md`. Follow those second-level documents because the entry page does not contain every price.

### Zhipu BigModel

- Public page: `https://bigmodel.cn/pricing`
- Anonymous page configuration: `https://bigmodel.cn/api/biz/operation/query?ids=1160%2C1161`
- The configuration is more stable and structured than scraping the rendered card text. Preserve crossed-out list prices and current promotional prices separately.

### MiniMax

- Official pay-as-you-go Markdown: `https://platform.minimaxi.com/docs/guides/pricing-paygo.md`
- Token Plan page: `https://platform.minimaxi.com/subscribe/token-plan?tab=api-enterprise`
- Use pay-as-you-go pricing for the default model comparison. Token Plan is a subscription product and must be described separately.
- Preserve standard versus priority service tiers, context-length tiers, cache reads, cache writes, historical status, and displayed discounts.

