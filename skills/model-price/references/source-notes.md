# Official sources

Prefer public JSON used by the official page; otherwise parse the official Markdown or HTML listed here.

- Aliyun Bailian: anonymous model-center JSON API in the script.
- Volcengine Ark: catalog and prices from `https://docs.volcengine.com/docs/82379/1544106`, using its public `getDocDetail` JSON and `Result.Content` rather than `MDContent`.
- Tencent Cloud TokenHub: embedded JSON from the official catalog and pricing documents; preserve self-deployed and “原厂直供” rows.
- DeepSeek, Kimi, Zhipu, MiniMax: official pricing pages or their public structured endpoints defined in the script.
- OpenAI: `https://developers.openai.com/api/docs/pricing`; no public pricing JSON is exposed, so use its official `.md` representation.
- Anthropic: `https://platform.claude.com/docs/en/about-claude/pricing`; no public pricing JSON is exposed, so use its official `.md` representation.
- Google Gemini: `https://ai.google.dev/gemini-api/docs/pricing`; no public pricing JSON or Markdown representation is exposed, so parse its server-rendered pricing tables and paid tier.

Provider caches live under `cache/<provider>/`. A cache entry records its provider, operation, arguments, fetch time, schema version, and data. Entries older than 3 hours are not used as fallback when refresh fails.
