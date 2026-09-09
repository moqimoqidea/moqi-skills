# 中国模型价格匿名查询与 Agent Skill

本文记录中国区模型目录与价格的公开查询方法。所有命令都删除了 Cookie、OAuth、Authorization、CSRF、账户 ID、控制台追踪参数和浏览器指纹。价格会变化，引用示例价格时应同时给出来源 URL、抓取时间、地域、币种、计费单位和计费条件。

验证日期：2026-09-09（Asia/Shanghai）。

## 结论

| 厂商 | 匿名来源 | 单模型价格 | 缓存价格 | 主要限制 |
|---|---|---:|---:|---|
| 阿里云百炼 | 控制台公开网关 API | 支持 | 支持缓存命中、显式缓存创建/读取 | `model` 仅接受一个字符串；目录每页最多 50 条 |
| 火山引擎方舟 | 模型广场公开 API | 支持 | 支持命中、存储及 Batch/Fast 相关项 | 单模型需同时传 `modelName` 和 `modelVersion` |
| 腾讯云 TokenHub | 官方公开文档 | 支持 | 支持缓存命中 | 控制台 `DescribeModelList` API 需要登录，不能匿名使用 |
| DeepSeek 原厂 | 官方价格页 | 支持 | 支持缓存命中 | 必须分别显示高峰与空闲时段 |
| 月之暗面 Kimi | 官方二级 Markdown | 支持 | 支持缓存命中 | 入口页不含完整价格，需继续读取二级链接 |
| 智谱 BigModel | 公开价格页配置 API | 支持 | 支持命中、存储说明 | 促销价和划线原价必须分开 |
| MiniMax 原厂 | 官方按量计费 Markdown | 支持 | 支持缓存读取和写入 | 标准/优先、上下文阶梯、历史模型不能混合 |

默认比价口径是中国区、人民币、在线推理标准价。缓存、Batch、阶梯、峰谷、优先服务和限时优惠单列；外币价格保留原币种。

## 六条可复用的 curl + jq 查询

以下六个代码块分别对应阿里目录、阿里单模型价格、火山目录、火山单模型价格、腾讯目录和腾讯单模型价格。每次价格查询只传一个模型。

### 1. 阿里云：获取完整 Qwen 模型 ID 列表

阿里接口每页最多 50 条，因此一个完整目录命令需要逐页请求，再由 `jq` 过滤 `qwen` 前缀和去重。

```bash
ALIYUN_URL='https://bailian-cs.console.aliyun.com/data/api.json?action=BroadScopeAspnGateway&product=sfm_bailian&api=zeldaHttp.dashscopeModel./zelda/api/v1/modelCenter/listFoundationModels&_v=undefined'

{
  page=1
  while :; do
    params=$(jq -cn --argjson page "$page" '{
      Api: "zeldaHttp.dashscopeModel./zelda/api/v1/modelCenter/listFoundationModels",
      Data: {
        input: {pageNo: $page, pageSize: 50},
        cornerstoneParam: {}
      }
    }')

    response=$(curl -q --silent --show-error --fail \
      --url "$ALIYUN_URL" \
      --data-urlencode "params=$params")

    data=$(jq -c '.data.DataV2.data.data' <<<"$response")
    jq -r '.list[]?.model | select(ascii_downcase | startswith("qwen"))' <<<"$data"

    total=$(jq -r '.total' <<<"$data")
    (( page * 50 >= total )) && break
    ((page++))
  done
} | sort -u | jq -Rsc 'split("\n") | map(select(length > 0)) | {count:length, models:.}'
```

验证快照中共有 509 个模型条目，其中 212 个 ID 以 `qwen` 开头。仓库中的 [qwen-model-ids.json](qwen-model-ids.json) 是当时的完整结果；实际使用应重新运行命令。

### 2. 阿里云：查询一个 Qwen 模型价格

```bash
MODEL='qwen3.8-max'
ALIYUN_URL='https://bailian-cs.console.aliyun.com/data/api.json?action=BroadScopeAspnGateway&product=sfm_bailian&api=zeldaHttp.dashscopeModel./zelda/api/v1/modelCenter/listFoundationModels&_v=undefined'

params=$(jq -cn --arg model "$MODEL" '{
  Api: "zeldaHttp.dashscopeModel./zelda/api/v1/modelCenter/listFoundationModels",
  Data: {
    input: {model: $model, queryPrice: true},
    cornerstoneParam: {}
  }
}')

curl -q --silent --show-error --fail \
  --url "$ALIYUN_URL" \
  --data-urlencode "params=$params" |
jq --arg model "$MODEL" '
  .data.DataV2.data.data.list
  | map((.items // [.])[])
  | map(select(.model == $model))
  | map({
      model,
      name,
      serviceSites,
      prices: [.prices[] | {
        type,
        name: .priceName,
        price: .price,
        unit: .priceUnit,
        discount: (.discount // null)
      }],
      priceTimeBands
    })'
```

2026-09-09 验证 `qwen3.8-max` 返回：输入 12 元/百万 tokens、输出 36、隐式缓存命中 1.5、显式缓存创建 15、显式缓存读取 1；还包含 Batch File 和 Batch Chat 项。Batch Chat 条目同时有原始价格与 `discount: 0.5`，计算有效价格时应保留折扣字段，不能把它误当普通在线价格。

### 3. 火山引擎：获取完整 Doubao 模型 ID 列表

```bash
curl -q --silent --show-error --fail \
  --url 'https://arkbff-cn-beijing.console.volcengine.com/api/2024-10-01/GetModelSquareTopData' \
  -H 'Content-Type: application/json' \
  --data-raw '{"viewType":"detail","modules":["pricing"]}' |
jq '[
  .Result.clientDetail.pricing[]?.selection.modelId
  | select(ascii_downcase | startswith("doubao"))
] | unique | {count:length, models:.}'
```

2026-09-09 重新验证时，默认公开目录包含 47 个当前模型选择项，其中 38 个完整 ID 以 `doubao` 开头。仓库中的 [doubao-model-ids.json](doubao-model-ids.json) 是这次验证快照。接口会随模型上下线而变化，旧快照曾包含 56 个 Doubao ID，因此运行时必须以实时目录为准。

### 4. 火山引擎：查询一个 Doubao 模型价格

```bash
MODEL_NAME='doubao-seed-2-1-pro'
MODEL_VERSION='260628'

curl -q --silent --show-error --fail \
  --url 'https://arkbff-cn-beijing.console.volcengine.com/api/2024-10-01/GetModelSquareTopData' \
  -H 'Content-Type: application/json' \
  --data-raw "$(jq -cn \
    --arg name "$MODEL_NAME" \
    --arg version "$MODEL_VERSION" '
    {
      viewType: "detail",
      selections: [{modelName: $name, modelVersion: $version}],
      modules: ["pricing"]
    }')" |
jq --arg id "${MODEL_NAME}-${MODEL_VERSION}" '[
  .Result.clientDetail.pricing[]
  | select(.selection.modelId == $id)
  | {
      selection,
      offers: [.section.pricing.segments[] | {
        key,
        label,
        prices: [.items[] | {key, label, value, unit}]
      }]
    }
]'
```

2026-09-09 验证 `doubao-seed-2-1-pro-260628` 的默认段包含：输入 6 元/百万 tokens、输出 30、缓存命中 1.2、缓存存储 0.017 元/百万 tokens/小时、批量输入 3、批量输出 15、批量缓存命中 1.2。因此火山的价格响应明确包含缓存相关价格。

### 5. 腾讯云：从官方文档获取完整模型 ID 列表

腾讯公开文档把表格放在内嵌路由状态中。下面的 `json_until` 同时兼容一层或多层 JSON 字符串编码。

```bash
curl -q --silent --show-error --fail --compressed \
  -H 'User-Agent: cn-cloud-model-price/1.0 (public-price-checker)' \
  --url 'https://cloud.tencent.com/document/product/1823/130051' |
jq -Rrs '
  def json_until:
    if type == "string" then (fromjson | json_until) else . end;
  def txt: [.. | objects | select(has("text")) | .text] | join("");
  def cell: [.children[]? | txt] | join("\n");

  capture("window\\.__staticRouterHydrationData\\s*=\\s*JSON\\.parse\\s*\\((?<state>\\\"(?:\\\\.|[^\\\"\\\\])*\\\")\\s*\\)").state
  | json_until
  | .loaderData["product-article"].data.article.content.slate
  | json_until
  | [
      .. | objects | select(.type? == "table")
      | [.children[]? | [.children[]? | select(.type? == "cell") | cell]]
      | . as $rows
      | ($rows[0] | to_entries
          | map(select(.value | contains("model（调用参数）")))
          | .[0].key) as $idx
      | select($idx != null)
      | $rows[1:][][$idx]
      | split("\n")[]
      | select(length > 0)
    ]
  | unique
  | {count:length, models:.}'
```

验证时得到 121 个模型调用 ID，包括 `glm-5.3` 和 `glm-5.3-flash`。

### 6. 腾讯云：从官方文档查询一个模型价格

```bash
MODEL='glm-5.3'

curl -q --silent --show-error --fail --compressed \
  -H 'User-Agent: cn-cloud-model-price/1.0 (public-price-checker)' \
  --url 'https://cloud.tencent.com/document/product/1823/130055' |
jq -Rrs --arg model "$MODEL" '
  def json_until:
    if type == "string" then (fromjson | json_until) else . end;
  def txt: [.. | objects | select(has("text")) | .text] | join("");
  def cell:
    [.children[]? | txt] | join(" ") | gsub("[[:space:]]+"; " ");

  capture("window\\.__staticRouterHydrationData\\s*=\\s*JSON\\.parse\\s*\\((?<state>\\\"(?:\\\\.|[^\\\"\\\\])*\\\")\\s*\\)").state
  | json_until
  | .loaderData["product-article"].data.article.content.slate
  | json_until
  | [
      .. | objects | select(.type? == "tab" and .name? == "广州")
      | .. | objects | select(.type? == "table")
      | [.children[]? | [.children[]? | select(.type? == "cell") | cell]]
      | select(.[0][0] == "模型名称")
      | .[1:][]
      | select((.[0] | ascii_downcase) == ($model | ascii_downcase))
      | {
          model: .[0],
          condition: .[1],
          time_band: .[2],
          input: .[3],
          output: .[4],
          cache_hit: .[5],
          unit: "元/百万 tokens",
          region: "广州"
        }
    ]'
```

2026-09-09 验证中国区广州的 `glm-5.3` 为输入 8 元/百万 tokens、输出 28、缓存命中 2。新加坡页签价格不同，不能与广州价格合并。

## 阿里请求参数说明与最小化验证

URL 查询参数：

| 参数 | 含义 | 最小价格请求是否需要 |
|---|---|---:|
| `action=BroadScopeAspnGateway` | 阿里控制台统一网关动作 | 需要 |
| `product=sfm_bailian` | 百炼产品路由标识 | 需要 |
| `api=zeldaHttp...listFoundationModels` | 网关内目标 API | 需要 |
| `_v=undefined` | 控制台前端生成的版本占位 | 当前命令保留以兼容网关；业务价格字段不依赖它 |

表单字段：

| 字段 | 含义 | 结论 |
|---|---|---|
| `params.Api` | 内部目标 API 名称 | 必填 |
| `params.V` | 网关协议版本 | 可删除 |
| `params.Data.input.model` | 一个模型 ID | 单模型查询必填；仅支持字符串，不支持数组或逗号多选 |
| `params.Data.input.queryPrice` | 要求返回 `prices` | 价格查询必填 |
| `params.Data.cornerstoneParam` | 控制台公共上下文容器 | 字段必须存在，但允许 `{}` |
| `region` | 控制台地域 | 此价格请求可删除 |
| `sec_token` | 原始抓包中的前端安全字段 | 可删除，匿名查询成功 |

原始请求中的 `pageNo`、`pageSize`、`group`、`querySampleCode`、`queryGroupByModel`、`queryWorkspaceLimit`、`queryQuota`、`queryQpmInfo`、`queryApplyStatus`、`queryPermissions`、`queryActivationStatus` 都不是单模型价格的必要参数。它们分别控制分页、分组、示例代码、工作区限制、额度、QPM、申请状态、权限和激活状态等附加数据。

明确传空的 `Cookie:` 或 `Authorization:` 请求头也不需要；直接省略即可。测试中未携带 token、OAuth、Cookie 或账户信息，仍返回 HTTP/业务成功码和价格数组。

## 火山请求参数说明与最小化验证

| 参数 | 含义 | 结论 |
|---|---|---|
| `viewType:"detail"` | 要求详情视图的数据结构 | 必填 |
| `modules:["pricing"]` | 只加载价格模块 | 必填；加入 `rateLimit` 才会额外返回限流数据 |
| `selections` | 一个或多个 `{modelName, modelVersion}` | 单模型价格查询传一个；省略时返回默认完整目录 |
| `region` | 控制台地域 | 当前公开价格请求可删除 |
| `Content-Type: application/json` | 声明 JSON 请求体 | 必填 |

原始抓包中的 Cookie、`userInfo`、`digest`、AccountID、CSRF、`x-web-id`、Origin、Referer、浏览器 UA 和 `sec-*` 请求头均已删除。匿名请求成功。

`modules:["pricing"]` 已经返回缓存价格；缓存展示不依赖 `rateLimit`。`rateLimit` 只控制限流信息，不控制定价字段。

## 腾讯控制台与公开文档

腾讯控制台模型详情内部使用 `DescribeModelList`，价格路径是 `Response.ModelSet[].ModelChargingInfo[].ChargingItems[]`，可以包含 `Input`、`Output` 和 `Cache`。但删除登录信息后会返回：

```json
{
  "code": 7,
  "msg": "登录态验证失败，请重新登录(UIN_OR_SKEY_MISSING)"
}
```

因此不能把控制台接口作为匿名 Skill 的运行来源。官方公开文档同时提供模型调用 ID、广州/新加坡地域价格以及缓存命中列，能够满足无需登录的价格查询和复核要求。

## 其他原厂来源

### DeepSeek

官方来源：[DeepSeek 模型与价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)。页面是匿名静态表格，包含模型 ID、缓存命中、缓存未命中输入和输出价格。当前价格分为空闲时段和高峰时段；高峰是北京时间周一至周五 09:00–12:00、14:00–18:00，其余为空闲时段。

### 月之暗面 Kimi

入口：[Kimi Chat 定价](https://platform.kimi.com/docs/pricing/chat)。它只给出二级链接，应继续读取官方 Markdown：

- [Kimi K3](https://platform.kimi.com/docs/pricing/chat-k3.md)
- [Kimi K2.7 Code](https://platform.kimi.com/docs/pricing/chat-k27-code.md)
- [Kimi K2.6](https://platform.kimi.com/docs/pricing/chat-k26.md)

2026-09-09 的公开价格为：

| 模型 | 缓存命中 | 缓存未命中输入 | 输出 | 单位 |
|---|---:|---:|---:|---|
| `kimi-k3` | 2.00 | 20.00 | 100.00 | 元/百万 tokens |
| `kimi-k2.7-code` | 1.30 | 6.50 | 27.00 | 元/百万 tokens |
| `kimi-k2.7-code-highspeed` | 2.60 | 13.00 | 54.00 | 元/百万 tokens |
| `kimi-k2.6` | 1.10 | 6.50 | 27.00 | 元/百万 tokens |

### 智谱 GLM

官方展示页：[智谱模型 API 价格](https://bigmodel.cn/pricing)。页面公开配置接口是：

```text
https://bigmodel.cn/api/biz/operation/query?ids=1160%2C1161
```

2026-09-09 配置中：

- `glm-5.3`：输入 8、输出 28、缓存命中 2 元/百万 tokens；缓存存储限时免费。
- `glm-5.3-flash`：当前限时 5 折，输入 0.4、输出 1.4、缓存命中 0.115；划线标准价分别为 0.8、2.8、0.23；缓存存储限时免费。

Skill 会分别保留当前价、`list_amount` 和促销标签，防止把限时价格当成长期标准价。

### MiniMax

用户提供的 [Token Plan 企业页](https://platform.minimaxi.com/subscribe/token-plan?tab=api-enterprise) 是订阅套餐，不等同于按量模型单价。默认比价使用 [官方按量计费 Markdown](https://platform.minimaxi.com/docs/guides/pricing-paygo.md)，Token Plan 仅作为补充方案单列。

按量文档包含标准/优先服务、输入长度阶梯、输入、输出、缓存读取和缓存写入。示例：`MiniMax-M3` 标准服务、输入不超过 512k tokens 的当前永久五折价是输入 2.10、输出 8.40、缓存读取 0.42 元/百万 tokens；划线价是 4.20、16.80、0.84。优先服务和超过 512k 的价格更高，不能合并到这一行。

### 火山官方目录与阿里官方价格页

- 火山模型目录：[模型列表](https://docs.volcengine.com/docs/82379/1544106?lang=zh)
- 阿里官方价格页：[模型列表与价格](https://help.aliyun.com/zh/model-studio/model-pricing)

阿里帮助页没有完整展示缓存价格，因此缓存相关查询继续使用已验证的匿名网关 API。火山帮助页适合人工复核模型范围，精确版本 ID 和缓存定价继续以公开模型广场 API 为准。

## Skill 使用

目录本身是一份尚未安装的 Skill。其他 Agent 可以在目录中运行：

```bash
python3 scripts/query_model_prices.py compare glm-5.3 --format markdown
python3 scripts/query_model_prices.py provider aliyun qwen3.8-max --format markdown
python3 scripts/query_model_prices.py provider volcengine doubao-seed-2-1-pro-260628 --format markdown
python3 scripts/query_model_prices.py list aliyun --prefix qwen
python3 scripts/query_model_prices.py list volcengine --prefix doubao
```

`compare` 会检查七家来源。输出将“精确找到”“来源中未找到精确模型”“来源解析失败”分开。以 2026-09-09 的 `glm-5.3` 实跑为例，腾讯云 TokenHub 和智谱原厂都精确提供该模型，价格都是输入 8、输出 28、缓存命中 2 元/百万 tokens；智谱另注明缓存存储限时免费。阿里、火山、DeepSeek、Kimi 和 MiniMax 的来源中未找到这个精确 ID。

## 数据与安全边界

- `qwen-model-ids.json` 和 `doubao-model-ids.json` 是验证时生成的快照，不能代替实时查询。
- `evidence/` 只保留最小化的匿名响应摘要，用于复核解析路径，不包含登录态。
- 价格字段保留字符串，避免浮点数舍入。
- 精确 ID 匹配优先；只在官方显示名映射唯一时接受显示名匹配。
- 不跨地域、版本、时间段、上下文区间或促销合并价格。
- 每次面向用户的价格结果都必须附来源 URL 和抓取时间，便于 double-check。
