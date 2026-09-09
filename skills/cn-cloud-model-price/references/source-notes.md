# Official sources

Use official structured data when available; only fall back to official HTML or Markdown when no public JSON source is exposed.

- Aliyun Bailian: anonymous model-center JSON API in the script. Paginate catalogs at 50 records and set `queryPrice:true` for prices.
- Volcengine Ark: both catalog and prices must come from `https://docs.volcengine.com/docs/82379/1544106`. Fetch its structured payload through `/api/doc/getDocDetail?DocumentID=1544106&LibraryID=82379&lang=zh` and parse `Result.Content` only when `ContentType` is `json`; do not use the model-square API or `MDContent`.
- Tencent Cloud TokenHub: catalog `https://cloud.tencent.com/document/product/1823/130051`, price `https://cloud.tencent.com/document/product/1823/130055`. Parse the embedded JSON router state and use Guangzhou for China-region comparisons. Preserve separate “原厂直供” and self-deployed rows.
- DeepSeek: `https://api-docs.deepseek.com/zh-cn/quick_start/pricing/`; preserve peak and off-peak prices.
- Kimi: `https://platform.kimi.com/docs/llms.txt` and its linked official pricing documents.
- Zhipu BigModel: structured public page configuration API used by `https://bigmodel.cn/pricing`; preserve promotional and list prices.
- MiniMax: `https://platform.minimaxi.com/docs/guides/pricing-paygo.md`; preserve service and context tiers.

Do not substitute third-party aggregators or authenticated console APIs for these sources.
