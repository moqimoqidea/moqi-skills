#!/usr/bin/env python3
"""Query public model catalogs and prices without credentials.

The adapters preserve provider-specific conditions instead of merging prices
across regions, time bands, context tiers, or promotions.
"""

from __future__ import annotations

import argparse
import gzip
import html
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Iterable


ALIYUN_URL = (
    "https://bailian-cs.console.aliyun.com/data/api.json"
    "?action=BroadScopeAspnGateway&product=sfm_bailian"
    "&api=zeldaHttp.dashscopeModel./zelda/api/v1/modelCenter/listFoundationModels"
    "&_v=undefined"
)
VOLCENGINE_URL = (
    "https://arkbff-cn-beijing.console.volcengine.com/"
    "api/2024-10-01/GetModelSquareTopData"
)
TENCENT_LIST_URL = "https://cloud.tencent.com/document/product/1823/130051"
TENCENT_PRICE_URL = "https://cloud.tencent.com/document/product/1823/130055"
DEEPSEEK_URL = "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/"
KIMI_INDEX_URL = "https://platform.kimi.com/docs/llms.txt"
ZHIPU_PAGE_URL = "https://bigmodel.cn/pricing"
ZHIPU_CONFIG_URL = "https://bigmodel.cn/api/biz/operation/query?ids=1160%2C1161"
MINIMAX_URL = "https://platform.minimaxi.com/docs/guides/pricing-paygo.md"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def normalize_model(value: str) -> str:
    value = html.unescape(value).strip().lower().replace("_", "-")
    value = re.sub(r"\s+", "-", value)
    return re.sub(r"-+", "-", value)


def clean_text(value: str) -> str:
    value = html.unescape(re.sub(r"<[^>]+>", " ", value))
    value = value.replace("**", "").replace("~~", "")
    return re.sub(r"\s+", " ", value).strip()


def numeric_values(value: str) -> list[str]:
    return re.findall(r"(?<![\w.])\d+(?:\.\d+)?", clean_text(value))


def unit_code(label: str) -> str:
    normalized = label.lower().replace(" ", "")
    if "百万" in normalized or "1m" in normalized or "/m" in normalized:
        if "小时" in normalized:
            return "CNY_per_million_tokens_per_hour"
        return "CNY_per_million_tokens"
    if "万字符" in normalized:
        return "CNY_per_10k_characters"
    if "每次" in normalized or "/次" in normalized:
        return "CNY_per_request"
    return label or "provider_defined"


def price_item(
    kind: str,
    label: str,
    amount: str | None,
    unit: str,
    *,
    display: str | None = None,
    list_amount: str | None = None,
    discount: Any = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "type": kind,
        "label": label,
        "amount": amount,
        "unit": unit,
    }
    if display:
        item["display"] = display
    if list_amount is not None:
        item["list_amount"] = list_amount
    if discount is not None:
        item["discount"] = discount
    return item


def make_record(
    provider_id: str,
    provider_name: str,
    model_id: str,
    display_name: str,
    region: str,
    offers: list[dict[str, Any]],
    source_url: str,
    source_kind: str,
    retrieved_at: str,
    **extra: Any,
) -> dict[str, Any]:
    record = {
        "provider": {"id": provider_id, "name": provider_name},
        "model_id": model_id,
        "display_name": display_name,
        "region": region,
        "currency": "CNY",
        "offers": offers,
        "source": {
            "url": source_url,
            "kind": source_kind,
            "retrieved_at": retrieved_at,
        },
    }
    record.update(extra)
    return record


class SourceError(RuntimeError):
    pass


class HttpClient:
    def __init__(self, timeout: int = 30) -> None:
        self.timeout = timeout
        self.user_agent = "cn-cloud-model-price/1.0 (public-price-checker)"

    def request(
        self,
        url: str,
        *,
        method: str = "GET",
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> str:
        merged = {"Accept": "*/*", "User-Agent": self.user_agent}
        merged.update(headers or {})
        request = urllib.request.Request(url, data=data, headers=merged, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read()
                if body.startswith(b"\x1f\x8b"):
                    body = gzip.decompress(body)
                return body.decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError) as exc:
            raise SourceError(f"request failed: {exc}") from exc

    def get_text(self, url: str) -> str:
        return self.request(url)

    def post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        text = self.request(
            url,
            method="POST",
            data=json.dumps(payload, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise SourceError("source did not return JSON") from exc

    def post_form(self, url: str, fields: dict[str, str]) -> dict[str, Any]:
        text = self.request(
            url,
            method="POST",
            data=urllib.parse.urlencode(fields).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise SourceError("source did not return JSON") from exc


class PriceSource(ABC):
    provider_id: str
    provider_name: str
    source_url: str
    source_kind: str
    catalog_url: str | None = None

    def __init__(self, client: HttpClient) -> None:
        self.client = client

    @abstractmethod
    def query(self, model: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def list_models(self, prefix: str = "") -> list[str]:
        raise NotImplementedError


ALIYUN_PRICE_TYPES = {
    "input_token": "input",
    "output_token": "output",
    "input_token_cache": "cache_hit",
    "input_token_cache_creation_5m": "cache_write",
    "input_token_cache_read": "cache_read_explicit",
    "input_token_batch": "batch_input",
    "output_token_batch": "batch_output",
    "input_token_batch_chat": "batch_chat_input",
    "output_token_batch_chat": "batch_chat_output",
}


class AliyunAdapter(PriceSource):
    provider_id = "aliyun"
    provider_name = "阿里云百炼"
    source_url = ALIYUN_URL
    source_kind = "anonymous_api"
    api_name = "zeldaHttp.dashscopeModel./zelda/api/v1/modelCenter/listFoundationModels"

    def _request(self, input_data: dict[str, Any]) -> dict[str, Any]:
        params = {
            "Api": self.api_name,
            "Data": {"input": input_data, "cornerstoneParam": {}},
        }
        outer = self.client.post_form(ALIYUN_URL, {"params": json.dumps(params)})
        try:
            data = outer["data"]["DataV2"]["data"]
            if str(data.get("code")) != "200":
                raise SourceError(f"Aliyun returned code {data.get('code')}")
            return data["data"]
        except (KeyError, TypeError) as exc:
            raise SourceError("unexpected Aliyun response shape") from exc

    def list_models(self, prefix: str = "") -> list[str]:
        models: set[str] = set()
        page = 1
        total = None
        while total is None or (page - 1) * 50 < total:
            data = self._request({"pageNo": page, "pageSize": 50})
            total = int(data.get("total", 0))
            for item in data.get("list", []):
                model = item.get("model")
                if model and (not prefix or normalize_model(model).startswith(normalize_model(prefix))):
                    models.add(model)
            page += 1
            if page > 100:
                raise SourceError("Aliyun pagination exceeded safety limit")
        return sorted(models, key=str.lower)

    def query(self, model: str) -> list[dict[str, Any]]:
        retrieved_at = now_iso()
        data = self._request({"queryPrice": True, "model": model})
        candidates: list[dict[str, Any]] = []
        for item in data.get("list", []):
            candidates.extend(item.get("items") or [item])
        result = []
        for item in candidates:
            if normalize_model(item.get("model", "")) != normalize_model(model):
                continue
            prices = [
                price_item(
                    ALIYUN_PRICE_TYPES.get(p.get("type"), p.get("type", "other")),
                    p.get("priceName", p.get("type", "价格")),
                    str(p["price"]) if p.get("price") is not None else None,
                    unit_code(p.get("priceUnit", "")),
                    discount=p.get("discount"),
                )
                for p in item.get("prices", [])
            ]
            result.append(
                make_record(
                    self.provider_id,
                    self.provider_name,
                    item["model"],
                    item.get("name", item["model"]),
                    "中国区",
                    [{"name": "online_standard", "conditions": {}, "prices": prices}],
                    self.source_url,
                    self.source_kind,
                    retrieved_at,
                    price_time_bands=item.get("priceTimeBands", []),
                    service_sites=item.get("serviceSites", []),
                )
            )
        return result


VOLC_PRICE_TYPES = {
    "InferencePrompt": "input",
    "InferenceCompletion": "output",
    "ContextSessionHit": "cache_hit",
    "ContextSessionStorage": "cache_storage",
    "BatchInferencePrompt": "batch_input",
    "BatchInferenceCompletion": "batch_output",
    "BatchInferenceCacheHit": "batch_cache_hit",
}


class VolcengineAdapter(PriceSource):
    provider_id = "volcengine"
    provider_name = "火山引擎方舟"
    source_url = VOLCENGINE_URL
    source_kind = "anonymous_api"

    def _bundle(self) -> dict[str, Any]:
        return self.client.post_json(
            VOLCENGINE_URL, {"viewType": "detail", "modules": ["pricing"]}
        )

    @staticmethod
    def _pricing_nodes(bundle: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            return bundle["Result"]["clientDetail"]["pricing"]
        except (KeyError, TypeError) as exc:
            raise SourceError("unexpected Volcengine response shape") from exc

    def list_models(self, prefix: str = "") -> list[str]:
        models = {
            node["selection"]["modelId"]
            for node in self._pricing_nodes(self._bundle())
            if node.get("selection", {}).get("modelId")
        }
        if prefix:
            normalized = normalize_model(prefix)
            models = {m for m in models if normalize_model(m).startswith(normalized)}
        return sorted(models, key=str.lower)

    def query(self, model: str) -> list[dict[str, Any]]:
        catalog = self._pricing_nodes(self._bundle())
        exact = [
            node["selection"]
            for node in catalog
            if normalize_model(node.get("selection", {}).get("modelId", ""))
            == normalize_model(model)
        ]
        if not exact:
            by_name = [
                node["selection"]
                for node in catalog
                if normalize_model(node.get("selection", {}).get("modelName", ""))
                == normalize_model(model)
            ]
            if len(by_name) == 1:
                exact = by_name
        if not exact:
            return []
        selection = exact[0]
        payload = {
            "viewType": "detail",
            "selections": [
                {
                    "modelName": selection["modelName"],
                    "modelVersion": selection["modelVersion"],
                }
            ],
            "modules": ["pricing"],
        }
        retrieved_at = now_iso()
        nodes = self._pricing_nodes(self.client.post_json(VOLCENGINE_URL, payload))
        records = []
        for node in nodes:
            selected = node.get("selection", {})
            if selected.get("modelId") != selection.get("modelId"):
                continue
            offers = []
            segments = node.get("section", {}).get("pricing", {}).get("segments", [])
            for segment in segments:
                prices = []
                for item in segment.get("items", []):
                    raw_type = item.get("key", "").split(":")[-1]
                    prices.append(
                        price_item(
                            VOLC_PRICE_TYPES.get(raw_type, raw_type or "other"),
                            item.get("label", raw_type),
                            str(item["value"]) if item.get("value") is not None else None,
                            unit_code(item.get("unit", "")),
                        )
                    )
                offers.append(
                    {
                        "name": segment.get("key", "default"),
                        "label": segment.get("label", "默认"),
                        "conditions": {},
                        "prices": prices,
                    }
                )
            records.append(
                make_record(
                    self.provider_id,
                    self.provider_name,
                    selected["modelId"],
                    selected["modelId"],
                    "中国区（北京）",
                    offers,
                    self.source_url,
                    self.source_kind,
                    retrieved_at,
                )
            )
        return records


def extract_tencent_slate(page: str) -> list[dict[str, Any]]:
    match = re.search(
        r"window\.__staticRouterHydrationData\s*=\s*JSON\.parse\s*\("
        r"(?P<quoted>\"(?:\\.|[^\"\\])*\")\s*\)",
        page,
    )
    if not match:
        raise SourceError("Tencent document state was not found")
    try:
        state = json.loads(json.loads(match.group("quoted")))
        slate: Any = state["loaderData"]["product-article"]["data"]["article"]["content"]["slate"]
        for _ in range(3):
            if not isinstance(slate, str):
                break
            slate = json.loads(slate)
        if not isinstance(slate, list):
            raise SourceError("Tencent slate is not a node list")
        return slate
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise SourceError("unexpected Tencent document state") from exc


def object_text(node: Any) -> str:
    if isinstance(node, dict):
        own = str(node.get("text", ""))
        return own + "".join(object_text(v) for k, v in node.items() if k != "text")
    if isinstance(node, list):
        return "".join(object_text(v) for v in node)
    return ""


def cell_text(cell: dict[str, Any]) -> str:
    paragraphs = []
    for child in cell.get("children", []):
        text = re.sub(r"\s+", " ", object_text(child)).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def walk_objects(node: Any) -> Iterable[dict[str, Any]]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk_objects(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk_objects(value)


def expand_slate_table(table: dict[str, Any]) -> list[list[str]]:
    grid: list[list[str]] = []
    spans: dict[int, tuple[int, str]] = {}
    for raw_row in table.get("children", []):
        if raw_row.get("type") != "row":
            continue
        row: list[str | None] = []

        def fill_spans_through(column: int) -> None:
            while len(row) <= column:
                row.append(None)
            if column in spans and row[column] is None:
                remaining, value = spans[column]
                row[column] = value
                if remaining <= 1:
                    del spans[column]
                else:
                    spans[column] = (remaining - 1, value)

        column = 0
        cells = [c for c in raw_row.get("children", []) if c.get("type") == "cell"]
        for cell in cells:
            while column in spans:
                fill_spans_through(column)
                column += 1
            value = cell_text(cell)
            colspan = max(1, int(cell.get("colSpan", 1)))
            rowspan = max(1, int(cell.get("rowSpan", 1)))
            for offset in range(colspan):
                while len(row) <= column + offset:
                    row.append(None)
                row[column + offset] = value
                if rowspan > 1:
                    spans[column + offset] = (rowspan - 1, value)
            column += colspan
        if spans:
            for position in sorted(list(spans)):
                fill_spans_through(position)
        grid.append([value or "" for value in row])
    return grid


class TencentAdapter(PriceSource):
    provider_id = "tencent"
    provider_name = "腾讯云 TokenHub"
    source_url = TENCENT_PRICE_URL
    source_kind = "official_document"
    catalog_url = TENCENT_LIST_URL

    def _catalog(self) -> list[tuple[str, str]]:
        slate = extract_tencent_slate(self.client.get_text(TENCENT_LIST_URL))
        pairs: list[tuple[str, str]] = []
        for node in walk_objects(slate):
            if node.get("type") != "table":
                continue
            rows = expand_slate_table(node)
            if not rows:
                continue
            headers = rows[0]
            try:
                name_index = next(i for i, h in enumerate(headers) if h == "模型名称")
                id_index = next(i for i, h in enumerate(headers) if "model（调用参数）" in h)
            except StopIteration:
                continue
            for row in rows[1:]:
                if max(name_index, id_index) >= len(row):
                    continue
                for model_id in row[id_index].splitlines():
                    model_id = model_id.strip()
                    if model_id:
                        pairs.append((model_id, row[name_index].strip()))
        return pairs

    def list_models(self, prefix: str = "") -> list[str]:
        models = {model_id for model_id, _ in self._catalog()}
        if prefix:
            normalized = normalize_model(prefix)
            models = {m for m in models if normalize_model(m).startswith(normalized)}
        return sorted(models, key=str.lower)

    def query(self, model: str) -> list[dict[str, Any]]:
        catalog = self._catalog()
        matches = [pair for pair in catalog if normalize_model(pair[0]) == normalize_model(model)]
        if not matches:
            display_matches = [
                pair for pair in catalog if normalize_model(pair[1]) == normalize_model(model)
            ]
            if len(display_matches) == 1:
                matches = display_matches
        if not matches:
            return []
        model_id, display_name = matches[0]
        retrieved_at = now_iso()
        slate = extract_tencent_slate(self.client.get_text(TENCENT_PRICE_URL))
        offers = []
        for node in walk_objects(slate):
            if node.get("type") != "tab" or node.get("name") != "广州":
                continue
            for child in walk_objects(node.get("children", [])):
                if child.get("type") != "table":
                    continue
                rows = expand_slate_table(child)
                if not rows:
                    continue
                headers = rows[0]
                if not any("推理输入" in h for h in headers):
                    continue

                def index_containing(label: str) -> int | None:
                    return next((i for i, h in enumerate(headers) if label in h), None)

                indexes = {
                    "name": index_containing("模型名称"),
                    "condition": index_containing("条件"),
                    "time_band": index_containing("峰谷计费"),
                    "input": index_containing("推理输入"),
                    "output": index_containing("推理输出"),
                    "cache_hit": index_containing("缓存命中"),
                }
                for row in rows[1:]:
                    name_pos = indexes["name"]
                    if name_pos is None or name_pos >= len(row):
                        continue
                    if normalize_model(row[name_pos]) != normalize_model(display_name):
                        continue
                    prices = []
                    for kind in ("input", "output", "cache_hit"):
                        position = indexes[kind]
                        if position is None or position >= len(row) or row[position] in ("", "-"):
                            continue
                        prices.append(
                            price_item(
                                kind,
                                clean_text(headers[position].split("（", 1)[0]),
                                row[position],
                                "CNY_per_million_tokens",
                            )
                        )
                    conditions = {}
                    for key in ("condition", "time_band"):
                        position = indexes[key]
                        if position is not None and position < len(row) and row[position] not in ("", "-"):
                            conditions[key] = row[position]
                    offers.append(
                        {
                            "name": "online_standard" if not conditions else "online_conditional",
                            "conditions": conditions,
                            "prices": prices,
                        }
                    )
        if not offers:
            return []
        return [
            make_record(
                self.provider_id,
                self.provider_name,
                model_id,
                display_name,
                "中国区（广州）",
                offers,
                self.source_url,
                self.source_kind,
                retrieved_at,
                catalog_url=TENCENT_LIST_URL,
            )
        ]


class TextTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self.table: list[list[str]] | None = None
        self.row: list[str] | None = None
        self.cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self.table = []
        elif self.table is not None and tag == "tr":
            self.row = []
        elif self.row is not None and tag in ("td", "th"):
            self.cell = []
        elif self.cell is not None and tag == "br":
            self.cell.append(" ")

    def handle_data(self, data: str) -> None:
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self.cell is not None and self.row is not None:
            self.row.append(re.sub(r"\s+", " ", "".join(self.cell)).strip())
            self.cell = None
        elif tag == "tr" and self.row is not None and self.table is not None:
            if self.row:
                self.table.append(self.row)
            self.row = None
        elif tag == "table" and self.table is not None:
            self.tables.append(self.table)
            self.table = None


class DeepSeekAdapter(PriceSource):
    provider_id = "deepseek"
    provider_name = "DeepSeek 原厂"
    source_url = DEEPSEEK_URL
    source_kind = "official_document"

    def _table(self) -> list[list[str]]:
        parser = TextTableParser()
        parser.feed(self.client.get_text(DEEPSEEK_URL).replace("\x00", ""))
        table = next((t for t in parser.tables if t and t[0] and t[0][0] == "模型"), None)
        if not table:
            raise SourceError("DeepSeek pricing table was not found")
        return table

    def list_models(self, prefix: str = "") -> list[str]:
        models = self._table()[0][1:]
        if prefix:
            normalized = normalize_model(prefix)
            models = [m for m in models if normalize_model(m).startswith(normalized)]
        return models

    def query(self, model: str) -> list[dict[str, Any]]:
        table = self._table()
        models = table[0][1:]
        try:
            model_index = next(i for i, item in enumerate(models) if normalize_model(item) == normalize_model(model))
        except StopIteration:
            return []
        type_map = {
            "缓存命中": "cache_hit",
            "缓存未命中": "input",
            "输出": "output",
        }
        offers: dict[str, dict[str, Any]] = {}
        current_kind = ""
        current_label = ""
        for row in table:
            joined = " ".join(row)
            detected = next((value for label, value in type_map.items() if label in joined), None)
            if detected:
                current_kind = detected
                current_label = next(label for label in type_map if label in joined)
            band = next((b for b in ("空闲时段", "高峰时段") if b in row), None)
            if not band or not current_kind:
                continue
            amounts = [re.sub(r"元$", "", value) for value in row if re.fullmatch(r"\d+(?:\.\d+)?元", value)]
            if len(amounts) != len(models):
                continue
            offer = offers.setdefault(
                band,
                {
                    "name": "off_peak" if band == "空闲时段" else "peak",
                    "conditions": {
                        "time_band": band,
                        "definition": (
                            "高峰：北京时间周一至周五 9:00–12:00、14:00–18:00；"
                            "其余为空闲时段"
                        ),
                    },
                    "prices": [],
                },
            )
            offer["prices"].append(
                price_item(
                    current_kind,
                    current_label,
                    amounts[model_index],
                    "CNY_per_million_tokens",
                )
            )
        return [
            make_record(
                self.provider_id,
                self.provider_name,
                models[model_index],
                models[model_index],
                "中国区",
                list(offers.values()),
                self.source_url,
                self.source_kind,
                now_iso(),
            )
        ]


def markdown_json_rows(document: str) -> list[list[str]]:
    rows = []
    for line in document.splitlines():
        candidate = line.strip().rstrip(",")
        if not candidate.startswith("[") or not candidate.endswith("]"):
            continue
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, list) and value and isinstance(value[0], str):
            rows.append(value)
    return rows


class KimiAdapter(PriceSource):
    provider_id = "kimi"
    provider_name = "月之暗面 Kimi"
    source_url = KIMI_INDEX_URL
    source_kind = "official_markdown"

    def _documents(self) -> list[tuple[str, str]]:
        index = self.client.get_text(KIMI_INDEX_URL)
        urls = re.findall(
            r"https://platform\.kimi\.com/docs/pricing/chat-[^)\s]+\.md", index
        )
        return [(url, self.client.get_text(url)) for url in dict.fromkeys(urls)]

    def list_models(self, prefix: str = "") -> list[str]:
        models = {row[0] for _, doc in self._documents() for row in markdown_json_rows(doc)}
        if prefix:
            normalized = normalize_model(prefix)
            models = {m for m in models if normalize_model(m).startswith(normalized)}
        return sorted(models, key=str.lower)

    def query(self, model: str) -> list[dict[str, Any]]:
        for url, document in self._documents():
            for row in markdown_json_rows(document):
                if len(row) < 5 or normalize_model(row[0]) != normalize_model(model):
                    continue
                amounts = [re.sub(r"^[¥￥]", "", value) for value in row[2:5]]
                prices = [
                    price_item("cache_hit", "输入（缓存命中）", amounts[0], "CNY_per_million_tokens"),
                    price_item("input", "输入（缓存未命中）", amounts[1], "CNY_per_million_tokens"),
                    price_item("output", "输出", amounts[2], "CNY_per_million_tokens"),
                ]
                return [
                    make_record(
                        self.provider_id,
                        self.provider_name,
                        row[0],
                        row[0],
                        "中国区",
                        [{"name": "online_standard", "conditions": {}, "prices": prices}],
                        url,
                        self.source_kind,
                        now_iso(),
                    )
                ]
        return []


def parse_display_price(value: str, label: str) -> dict[str, Any]:
    cleaned = clean_text(value)
    values = numeric_values(value)
    if "免费" in cleaned:
        return price_item(
            "other", label, "0", unit_code(value), display=cleaned
        )
    amount = values[-1] if values else None
    list_amount = values[0] if len(values) > 1 else None
    return price_item(
        "other", label, amount, unit_code(value), display=cleaned, list_amount=list_amount
    )


class ZhipuAdapter(PriceSource):
    provider_id = "zhipu"
    provider_name = "智谱 BigModel"
    source_url = ZHIPU_PAGE_URL
    source_kind = "anonymous_config_api"

    def _cards(self) -> list[dict[str, Any]]:
        response = json.loads(self.client.get_text(ZHIPU_CONFIG_URL))
        if str(response.get("code")) != "200":
            raise SourceError(f"Zhipu returned code {response.get('code')}")
        cards = []
        for entry in response.get("data", []):
            config = json.loads(entry["content"])
            for card in config.get("list", []):
                values = []
                table = card.get("table", {})
                fields = [field.get("code") for field in table.get("fieldList", [])]
                if len(fields) >= 2:
                    for row in table.get("modelList", []):
                        left = row.get(fields[0], {}).get("value", "")
                        right = row.get(fields[1], {}).get("value", "")
                        values.append((clean_text(str(left)), str(right)))
                cards.append({**card, "values": values})
            for tab in config.get("tabs", []):
                if tab.get("title") != "模型":
                    continue
                for card in tab.get("cards", []):
                    values = [
                        (field.get("label", ""), " / ".join(map(str, field.get("values", []))))
                        for field in card.get("fieldList", [])
                    ]
                    cards.append({**card, "values": values})
        return cards

    def list_models(self, prefix: str = "") -> list[str]:
        models = {normalize_model(card.get("title", "")) for card in self._cards() if card.get("title")}
        if prefix:
            normalized = normalize_model(prefix)
            models = {m for m in models if m.startswith(normalized)}
        return sorted(models)

    def query(self, model: str) -> list[dict[str, Any]]:
        card = next(
            (c for c in self._cards() if normalize_model(c.get("title", "")) == normalize_model(model)),
            None,
        )
        if not card:
            return []
        type_map = {
            "输入单价": "input",
            "输入价格": "input",
            "输出单价": "output",
            "输出价格": "output",
            "缓存命中": "cache_hit",
            "缓存存储": "cache_storage",
        }
        prices = []
        notes = []
        for label, value in card.get("values", []):
            kind = next((kind for key, kind in type_map.items() if key in label), None)
            if kind:
                item = parse_display_price(value, label)
                item["type"] = kind
                prices.append(item)
            elif "Batch" in label or "定价" in label:
                notes.append(f"{label}：{clean_text(value)}")
        conditions = {}
        if card.get("tag") and re.search(r"折|限时|优惠|促销", card["tag"]):
            conditions["promotion"] = card["tag"]
        return [
            make_record(
                self.provider_id,
                self.provider_name,
                normalize_model(card["title"]),
                card["title"],
                "中国区",
                [{"name": "online_standard", "conditions": conditions, "prices": prices}],
                self.source_url,
                self.source_kind,
                now_iso(),
                config_url=ZHIPU_CONFIG_URL,
                notes=notes,
            )
        ]


def split_markdown_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


class MiniMaxAdapter(PriceSource):
    provider_id = "minimax"
    provider_name = "MiniMax 原厂"
    source_url = MINIMAX_URL
    source_kind = "official_markdown"

    def _rows(self) -> list[dict[str, Any]]:
        text = self.client.get_text(MINIMAX_URL)
        language = text.split("## 语言模型", 1)[1].split("## 语音", 1)[0]
        rows: list[dict[str, Any]] = []
        tier = "standard"
        historical = False
        for line in language.splitlines():
            tab = re.search(r'<Tab title="([^"]+)"', line)
            if tab:
                tier = "priority" if tab.group(1) == "优先*" else "standard"
            if "</Tabs>" in line:
                tier = "standard"
            if "<Accordion title=\"历史模型\">" in line:
                historical = True
            if "</Accordion>" in line:
                historical = False
            if not line.lstrip().startswith("|") or re.match(r"^\s*\|\s*:?-", line):
                continue
            cells = split_markdown_row(line)
            if not cells or "模型" in clean_text(cells[0]):
                continue
            model_parts = re.split(r"<br\s*/?>", cells[0], maxsplit=1)
            model_name = clean_text(model_parts[0])
            if not model_name or not numeric_values(" ".join(cells[1:])):
                continue
            condition = clean_text(model_parts[1]) if len(model_parts) > 1 else ""
            amounts = []
            list_amounts = []
            for cell in cells[1:]:
                values = numeric_values(cell)
                amounts.append(values[-1] if values else None)
                list_amounts.append(values[0] if len(values) > 1 else None)
            prices = []
            kinds = ["input", "output", "cache_hit", "cache_write"]
            labels = ["输入", "输出", "缓存读取", "缓存写入"]
            for index, amount in enumerate(amounts[:4]):
                if amount is not None:
                    prices.append(
                        price_item(
                            kinds[index],
                            labels[index],
                            amount,
                            "CNY_per_million_tokens",
                            list_amount=list_amounts[index],
                        )
                    )
            rows.append(
                {
                    "model": model_name,
                    "tier": tier,
                    "historical": historical,
                    "condition": condition,
                    "prices": prices,
                }
            )
        return rows

    def list_models(self, prefix: str = "") -> list[str]:
        models = {row["model"] for row in self._rows()}
        if prefix:
            normalized = normalize_model(prefix)
            models = {m for m in models if normalize_model(m).startswith(normalized)}
        return sorted(models, key=str.lower)

    def query(self, model: str) -> list[dict[str, Any]]:
        matched = [row for row in self._rows() if normalize_model(row["model"]) == normalize_model(model)]
        if not matched:
            return []
        offers = []
        for row in matched:
            conditions: dict[str, Any] = {"service_tier": row["tier"]}
            if row["condition"]:
                conditions["context_tier"] = row["condition"]
            if row["historical"]:
                conditions["status"] = "historical"
            offers.append(
                {
                    "name": row["tier"],
                    "conditions": conditions,
                    "prices": row["prices"],
                }
            )
        return [
            make_record(
                self.provider_id,
                self.provider_name,
                normalize_model(matched[0]["model"]),
                matched[0]["model"],
                "中国区",
                offers,
                self.source_url,
                self.source_kind,
                now_iso(),
            )
        ]


def build_adapters(client: HttpClient) -> dict[str, PriceSource]:
    adapters: list[PriceSource] = [
        AliyunAdapter(client),
        VolcengineAdapter(client),
        TencentAdapter(client),
        DeepSeekAdapter(client),
        KimiAdapter(client),
        ZhipuAdapter(client),
        MiniMaxAdapter(client),
    ]
    return {adapter.provider_id: adapter for adapter in adapters}


def source_status(adapter: PriceSource, status: str, retrieved_at: str, error: str | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {
        "provider": {"id": adapter.provider_id, "name": adapter.provider_name},
        "status": status,
        "source": {
            "url": adapter.source_url,
            "kind": adapter.source_kind,
            "retrieved_at": retrieved_at,
        },
    }
    if error:
        item["error"] = error
    return item


def query_adapters(adapters: Iterable[PriceSource], model: str) -> dict[str, Any]:
    started = now_iso()
    records = []
    checks = []
    for adapter in adapters:
        checked_at = now_iso()
        try:
            found = adapter.query(model)
            records.extend(found)
            checks.append(source_status(adapter, "available" if found else "not_found", checked_at))
        except Exception as exc:  # keep other providers usable when one source changes
            checks.append(source_status(adapter, "source_error", checked_at, str(exc)))
    return {"query": model, "retrieved_at": started, "results": records, "source_checks": checks}


def price_lookup(offer: dict[str, Any], kind: str) -> dict[str, Any] | None:
    return next((item for item in offer.get("prices", []) if item.get("type") == kind), None)


def format_price(item: dict[str, Any] | None) -> str:
    if not item:
        return "—"
    display = item.get("display")
    amount = item.get("amount")
    unit = item.get("unit")
    if display and "免费" in display:
        return display
    if amount is None:
        return display or "—"
    unit_labels = {
        "CNY_per_million_tokens": "元/百万 tokens",
        "CNY_per_million_tokens_per_hour": "元/百万 tokens/小时",
        "CNY_per_10k_characters": "元/万字符",
        "CNY_per_request": "元/次",
    }
    current = f"{amount} {unit_labels.get(unit, unit)}"
    if item.get("list_amount") is not None:
        current += f"（原价 {item['list_amount']}）"
    return current


def format_other_prices(offer: dict[str, Any]) -> str:
    core = {"input", "output", "cache_hit", "cache_write", "cache_storage"}
    items = []
    for item in offer.get("prices", []):
        if item.get("type") in core:
            continue
        value = format_price(item)
        discount = f"，discount={item['discount']}" if item.get("discount") is not None else ""
        items.append(f"{item.get('label', item.get('type', '价格'))}: {value}{discount}")
    return "；".join(items) or "—"


def to_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# 模型价格查询：`{payload.get('query', '')}`",
        "",
        f"抓取时间：{payload.get('retrieved_at', '')}",
        "",
    ]
    results = payload.get("results", [])
    if results:
        lines.extend(
            [
                "| 厂商 | 模型 ID | 地域 | 计费条件 | 输入 | 输出 | 缓存命中 | 缓存写入/存储 | 其他价格 | 来源 |",
                "|---|---|---|---|---:|---:|---:|---:|---|---|",
            ]
        )
        for record in results:
            for offer in record.get("offers", []):
                conditions = offer.get("conditions", {})
                condition_text = "；".join(f"{k}={v}" for k, v in conditions.items()) or offer.get("name", "标准")
                source = record["source"]["url"]
                lines.append(
                    "| {provider} | `{model}` | {region} | {conditions} | {input} | {output} | {cache} | {storage} | {other} | [官方来源]({source}) |".format(
                        provider=record["provider"]["name"],
                        model=record["model_id"],
                        region=record["region"],
                        conditions=condition_text.replace("|", "\\|"),
                        input=format_price(price_lookup(offer, "input")),
                        output=format_price(price_lookup(offer, "output")),
                        cache=format_price(price_lookup(offer, "cache_hit")),
                        storage=format_price(price_lookup(offer, "cache_write") or price_lookup(offer, "cache_storage")),
                        other=format_other_prices(offer).replace("|", "\\|"),
                        source=source,
                    )
                )
    else:
        lines.append("没有来源确认提供这个精确模型 ID。")
    lines.extend(["", "## 来源检查", ""])
    labels = {"available": "已找到", "not_found": "未找到精确模型", "source_error": "来源解析失败"}
    for check in payload.get("source_checks", []):
        source = check["source"]
        error = f"；{check['error']}" if check.get("error") else ""
        lines.append(
            f"- {check['provider']['name']}：{labels.get(check['status'], check['status'])}；"
            f"[{source['kind']}]({source['url']})；检查时间 {source['retrieved_at']}{error}"
        )
    return "\n".join(lines) + "\n"


def emit(payload: Any, output_format: str) -> None:
    if output_format == "markdown":
        print(to_markdown(payload), end="")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Query public Chinese model prices with sources")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds")
    subparsers = parser.add_subparsers(dest="command", required=True)

    compare = subparsers.add_parser("compare", help="compare one exact model across all providers")
    compare.add_argument("model")
    compare.add_argument("--format", choices=("json", "markdown"), default="json")

    provider = subparsers.add_parser("provider", help="query one provider for one exact model")
    provider.add_argument("provider")
    provider.add_argument("model")
    provider.add_argument("--format", choices=("json", "markdown"), default="json")

    listing = subparsers.add_parser("list", help="list model IDs from one provider")
    listing.add_argument("provider")
    listing.add_argument("--prefix", default="")
    listing.add_argument("--format", choices=("json",), default="json")

    args = parser.parse_args()
    adapters = build_adapters(HttpClient(args.timeout))
    selected_provider = getattr(args, "provider", None)
    if selected_provider is not None and selected_provider not in adapters:
        parser.error(f"unknown provider: {selected_provider}; choose from {', '.join(adapters)}")

    if args.command == "compare":
        emit(query_adapters(adapters.values(), args.model), args.format)
    elif args.command == "provider":
        emit(query_adapters([adapters[args.provider]], args.model), args.format)
    else:
        adapter = adapters[args.provider]
        checked_at = now_iso()
        try:
            models = adapter.list_models(args.prefix)
            payload = {
                "provider": {"id": adapter.provider_id, "name": adapter.provider_name},
                "prefix": args.prefix,
                "count": len(models),
                "models": models,
                "source": {
                    "url": adapter.catalog_url or adapter.source_url,
                    "kind": adapter.source_kind,
                    "retrieved_at": checked_at,
                },
            }
        except Exception as exc:
            payload = source_status(adapter, "source_error", checked_at, str(exc))
        emit(payload, args.format)
    return 0


if __name__ == "__main__":
    sys.exit(main())
