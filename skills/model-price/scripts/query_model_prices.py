#!/usr/bin/env python3
"""Query public model catalogs and prices without credentials.

The adapters preserve provider-specific conditions instead of merging prices
across regions, time bands, context tiers, or promotions.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

ALIYUN_URL = (
    "https://bailian-cs.console.aliyun.com/data/api.json"
    "?action=BroadScopeAspnGateway&product=sfm_bailian"
    "&api=zeldaHttp.dashscopeModel./zelda/api/v1/modelCenter/listFoundationModels"
    "&_v=undefined"
)
VOLCENGINE_PAGE_URL = "https://docs.volcengine.com/docs/82379/1544106"
VOLCENGINE_DOC_API = (
    "https://docs.volcengine.com/api/doc/getDocDetail"
    "?DocumentID=1544106&LibraryID=82379&lang=zh"
)
TENCENT_LIST_URL = "https://cloud.tencent.com/document/product/1823/130051"
TENCENT_PRICE_URL = "https://cloud.tencent.com/document/product/1823/130055"
DEEPSEEK_URL = "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/"
KIMI_INDEX_URL = "https://platform.kimi.com/docs/llms.txt"
ZHIPU_PAGE_URL = "https://bigmodel.cn/pricing"
ZHIPU_CONFIG_URL = "https://bigmodel.cn/api/biz/operation/query?ids=1160%2C1161"
MINIMAX_URL = "https://platform.minimaxi.com/docs/guides/pricing-paygo.md"
OPENAI_URL = "https://developers.openai.com/api/docs/pricing"
OPENAI_MARKDOWN_URL = f"{OPENAI_URL}.md"
ANTHROPIC_URL = "https://platform.claude.com/docs/en/about-claude/pricing"
ANTHROPIC_MARKDOWN_URL = f"{ANTHROPIC_URL}.md"
GEMINI_URL = "https://ai.google.dev/gemini-api/docs/pricing"

DOMESTIC_PROVIDER_IDS = (
    "aliyun",
    "volcengine",
    "tencent",
    "deepseek",
    "kimi",
    "zhipu",
    "minimax",
)
OVERSEAS_PROVIDER_IDS = ("openai", "anthropic", "google")
CACHE_TTL = timedelta(hours=3)
CACHE_SCHEMA_VERSION = 1
DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[1] / "cache"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


FOOTNOTE_MARKER_RE = re.compile(r"\s*[（(]\s*\d+\s*[)）]\s*$")

# Retired names that official docs still serve. Aliases only ever add matches;
# they never remove one, so family expansion for live names keeps working.
RETIRED_MODEL_ALIASES = {
    "deepseek-v4-flash": "deepseek-flash",
    "deepseek/deepseek-v4-flash-vision-exp": "deepseek-flash",
    "deepseek-v4-flash-vision-exp": "deepseek-flash",
}


def strip_footnote_markers(value: str) -> str:
    """Drop trailing citation markers such as ``deepseek-flash(1)``.

    Vendor docs annotate model columns with footnote numbers. Those markers are
    presentation only, so they must never take part in model identity.
    """
    result = value
    while True:
        stripped = FOOTNOTE_MARKER_RE.sub("", result).strip()
        if stripped == result:
            return stripped
        result = stripped


def normalize_model(value: str) -> str:
    value = strip_footnote_markers(html.unescape(value))
    value = value.strip().lower().replace("_", "-")
    value = re.sub(r"\s+", "-", value)
    return re.sub(r"-+", "-", value)


def model_key(value: str) -> str:
    """Normalize a model name and follow documented retired-name aliases."""
    key = normalize_model(value)
    return RETIRED_MODEL_ALIASES.get(key, key)


def model_family(value: str) -> str:
    """Return a comparison key while preserving meaningful model versions."""
    value = clean_text(value).lower()
    value = re.sub(r"\b(?:原厂直供|正式版|预览版)\b", "", value)
    value = value.replace("原厂直供", "").replace("正式版", "").replace("预览版", "")
    return normalize_model(value).strip("-").rsplit("/", 1)[-1]


def _raw_model_matches(query: str, candidate: str, *, exact: bool = False) -> bool:
    query_key = normalize_model(query)
    candidate_key = normalize_model(candidate)
    if exact:
        return candidate_key == query_key
    family = model_family(candidate)
    query_family = model_family(query)
    keys = {candidate_key, family, family.rsplit("/", 1)[-1]}
    return any(
        key == query_key
        or key == query_family
        or key.startswith(f"{query_key}-")
        or key.startswith(f"{query_family}-")
        for key in keys
    )


def model_matches(query: str, candidate: str, *, exact: bool = False) -> bool:
    if _raw_model_matches(query, candidate, exact=exact):
        return True
    alias = RETIRED_MODEL_ALIASES.get(normalize_model(query))
    if not alias:
        return False
    return _raw_model_matches(alias, candidate, exact=exact)


def tencent_delivery_mode(display_name: str) -> str:
    return "upstream_direct" if "原厂直供" in display_name else "self_deployed"


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
        self.user_agent = "model-price/2.0 (public-price-checker)"

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

    def search(self, model: str, *, exact: bool = False) -> list[dict[str, Any]]:
        if exact:
            return self.query(model)
        records: list[dict[str, Any]] = []
        for candidate in self.list_models():
            if model_matches(model, candidate):
                records.extend(self.query(candidate))
        return records


class CacheStore:
    """Small file cache scoped by provider and operation."""

    def __init__(
        self,
        root: Path = DEFAULT_CACHE_DIR,
        ttl: timedelta = CACHE_TTL,
        clock: Any = None,
    ) -> None:
        self.root = root
        self.ttl = ttl
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _path(self, provider: str, operation: str, arguments: Any) -> Path:
        identity = json.dumps(
            [operation, arguments],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(identity.encode()).hexdigest()[:20]
        return self.root / provider / f"{operation}-{digest}.json"

    def read(
        self, provider: str, operation: str, arguments: Any
    ) -> tuple[Any, str] | None:
        path = self._path(provider, operation, arguments)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("schema_version") != CACHE_SCHEMA_VERSION:
                return None
            fetched_at = datetime.fromisoformat(payload["fetched_at"])
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=timezone.utc)
            if self.clock() - fetched_at > self.ttl:
                return None
            return payload["data"], payload["fetched_at"]
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def write(self, provider: str, operation: str, arguments: Any, data: Any) -> str:
        path = self._path(provider, operation, arguments)
        path.parent.mkdir(parents=True, exist_ok=True)
        fetched_at = self.clock().astimezone().isoformat(timespec="seconds")
        payload = {
            "schema_version": CACHE_SCHEMA_VERSION,
            "provider": provider,
            "operation": operation,
            "arguments": arguments,
            "fetched_at": fetched_at,
            "data": data,
        }
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", dir=path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return fetched_at


class CachedPriceSource(PriceSource):
    """Cache decorator; source adapters stay focused on parsing official data."""

    def __init__(
        self, source: PriceSource, cache: CacheStore, *, refresh: bool = False
    ) -> None:
        self.source = source
        self.cache = cache
        self.refresh = refresh
        self.provider_id = source.provider_id
        self.provider_name = source.provider_name
        self.source_url = source.source_url
        self.source_kind = source.source_kind
        self.catalog_url = source.catalog_url
        self.cache_status = "unused"
        self.cached_at: str | None = None

    def _cached(self, operation: str, arguments: Any, loader: Any) -> Any:
        if not self.refresh:
            cached = self.cache.read(self.provider_id, operation, arguments)
            if cached is not None:
                data, self.cached_at = cached
                self.cache_status = "hit"
                return data
        try:
            data = loader()
        except Exception:
            self.cache_status = "refresh_failed"
            raise
        self.cached_at = self.cache.write(self.provider_id, operation, arguments, data)
        self.cache_status = "refreshed" if self.refresh else "miss"
        return data

    def list_models(self, prefix: str = "") -> list[str]:
        return self._cached(
            "list", {"prefix": prefix}, lambda: self.source.list_models(prefix)
        )

    def query(self, model: str) -> list[dict[str, Any]]:
        return self._cached("query", {"model": model}, lambda: self.source.query(model))

    def search(self, model: str, *, exact: bool = False) -> list[dict[str, Any]]:
        return self._cached(
            "search",
            {"model": model, "exact": exact},
            lambda: self.source.search(model, exact=exact),
        )


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
                if model and (
                    not prefix
                    or normalize_model(model).startswith(normalize_model(prefix))
                ):
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
            grouped_prices: dict[str, list[dict[str, Any]]] = {}
            for price in item.get("prices", []):
                band = price.get("timeBand") or "standard"
                grouped_prices.setdefault(band, []).append(
                    price_item(
                        ALIYUN_PRICE_TYPES.get(
                            price.get("type"), price.get("type", "other")
                        ),
                        price.get("priceName", price.get("type", "价格")),
                        str(price["price"]) if price.get("price") is not None else None,
                        unit_code(price.get("priceUnit", "")),
                        discount=price.get("discount"),
                    )
                )
            offers = []
            for band, prices in grouped_prices.items():
                conditions = {} if band == "standard" else {"time_band": band}
                offers.append(
                    {"name": band, "conditions": conditions, "prices": prices}
                )
            result.append(
                make_record(
                    self.provider_id,
                    self.provider_name,
                    item["model"],
                    item.get("name", item["model"]),
                    "中国区",
                    offers,
                    self.source_url,
                    self.source_kind,
                    retrieved_at,
                    price_time_bands=item.get("priceTimeBands", []),
                    service_sites=item.get("serviceSites", []),
                    delivery_mode=(
                        "platform_hosted"
                        if item.get("inferenceProvider") == "aliyun-bailian"
                        else "third_party_hosted"
                    ),
                    inference_provider=item.get("inferenceProvider"),
                    access_scope=item.get("scope"),
                    model_family=model_family(item["model"]),
                )
            )
        return result


class VolcDocument:
    """Read Volcengine's structured document JSON and its embedded tables."""

    def __init__(self, payload: dict[str, Any]) -> None:
        try:
            result = payload["Result"]
            if result.get("ContentType") != "json":
                raise SourceError("Volcengine pricing document is not structured JSON")
            content = json.loads(result["Content"])
            self.data = content["data"]
            self.updated_at = result.get("UpdatedTime")
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise SourceError("unexpected Volcengine document response") from exc

    def _text(self, zone_id: str, seen: set[str] | None = None) -> str:
        seen = set() if seen is None else seen
        if zone_id in seen:
            return ""
        seen.add(zone_id)
        pieces = []
        for op in self.data.get(zone_id, {}).get("ops", []):
            value = op.get("insert", "")
            if isinstance(value, str):
                if value == "*" and op.get("attributes", {}).get("lineId"):
                    continue
                pieces.append(value)
            elif isinstance(value, dict) and value.get("id"):
                pieces.append(self._text(value["id"], seen))
        raw = "".join(pieces)
        return "\n".join(
            line for line in (clean_text(line) for line in raw.splitlines()) if line
        )

    def _table(self, reference: str) -> list[list[str]]:
        try:
            row_zone, column_zone = reference.split()[:2]
            rows = [
                op["insert"]["id"]
                for op in self.data[row_zone]["ops"]
                if isinstance(op.get("insert"), dict) and op["insert"].get("id")
            ]
            columns = [
                op["insert"]["id"]
                for op in self.data[column_zone]["ops"]
                if isinstance(op.get("insert"), dict) and op["insert"].get("id")
            ]
        except (KeyError, ValueError) as exc:
            raise SourceError("unexpected Volcengine table structure") from exc
        return [
            [self._text(f"x{row_id}x{column_id}") for column_id in columns]
            for row_id in rows
        ]

    def tables(self) -> Iterable[tuple[list[str], list[list[str]]]]:
        headings: list[str] = []
        pending_level: int | None = None
        try:
            operations = self.data["0"]["ops"]
        except (KeyError, TypeError) as exc:
            raise SourceError("Volcengine document root was not found") from exc
        for op in operations:
            attributes = op.get("attributes", {})
            heading = attributes.get("heading")
            if isinstance(heading, str) and heading.startswith("h"):
                pending_level = int(heading[1:])
            value = op.get("insert")
            if (
                pending_level
                and isinstance(value, str)
                and value.strip() not in ("", "*")
            ):
                title = clean_text(value)
                headings = headings[: pending_level - 1]
                headings.append(title)
                pending_level = None
            if attributes.get("aceTable"):
                yield list(headings), self._table(attributes["aceTable"])


def volc_offer_name(headings: list[str]) -> str:
    title = " / ".join(headings)
    if "低延迟" in title:
        return "online_low_latency"
    if "批量推理" in title:
        return "batch"
    if "TPM" in title:
        return "tpm_package"
    return "online_standard"


def price_type_from_header(header: str) -> str:
    compact = clean_text(header).replace(" ", "")
    if "缓存存储" in compact:
        return "cache_storage"
    if "缓存命中" in compact and "音频" in compact and "非音频" not in compact:
        return "audio_cache_hit"
    if "缓存命中" in compact:
        return "cache_hit"
    if "输入" in compact and "音频" in compact and "非音频" not in compact:
        return "audio_input"
    if "输入" in compact:
        return "input"
    if "输出" in compact:
        return "output"
    return "other"


def amount_from_cell(value: str) -> str | None:
    if clean_text(value) in ("", "-"):
        return None
    values = numeric_values(value)
    return values[-1] if values else None


class VolcengineAdapter(PriceSource):
    provider_id = "volcengine"
    provider_name = "火山引擎方舟"
    source_url = VOLCENGINE_PAGE_URL
    source_kind = "official_document_json"
    catalog_url = VOLCENGINE_PAGE_URL

    def _document(self) -> VolcDocument:
        return VolcDocument(json.loads(self.client.get_text(VOLCENGINE_DOC_API)))

    def _records(self) -> list[dict[str, Any]]:
        document = self._document()
        retrieved_at = now_iso()
        grouped: dict[str, dict[str, Any]] = {}
        for headings, table in document.tables():
            if len(table) < 2:
                continue
            headers = table[0]
            model_index = next(
                (
                    i
                    for i, header in enumerate(headers)
                    if clean_text(header) in ("模型", "模型名称")
                ),
                None,
            )
            if model_index is None or not any(
                price_type_from_header(h) != "other" for h in headers
            ):
                continue
            condition_index = next(
                (i for i, header in enumerate(headers) if "条件" in clean_text(header)),
                None,
            )
            current_model_cell = ""
            for row in table[1:]:
                if model_index >= len(row):
                    continue
                raw_model_cell = row[model_index]
                if clean_text(raw_model_cell):
                    current_model_cell = raw_model_cell
                if not current_model_cell or "不适用" in current_model_cell:
                    continue
                display_name = clean_text(current_model_cell.splitlines()[0])
                prices = []
                for index, header in enumerate(headers):
                    kind = price_type_from_header(header)
                    if kind == "other" or index >= len(row):
                        continue
                    amount = amount_from_cell(row[index])
                    if amount is not None:
                        prices.append(
                            price_item(
                                kind, clean_text(header), amount, unit_code(header)
                            )
                        )
                if not prices:
                    continue
                conditions: dict[str, Any] = {}
                if condition_index is not None and condition_index < len(row):
                    condition = clean_text(row[condition_index])
                    if condition not in ("", "-"):
                        conditions["context_tier"] = condition
                stage = "preview" if "预览版" in display_name else "stable"
                conditions["release_stage"] = stage
                key = normalize_model(display_name)
                record = grouped.setdefault(
                    key,
                    make_record(
                        self.provider_id,
                        self.provider_name,
                        key,
                        display_name,
                        "中国区",
                        [],
                        self.source_url,
                        self.source_kind,
                        retrieved_at,
                        source_api=VOLCENGINE_DOC_API,
                        source_updated_at=document.updated_at,
                        delivery_mode="platform_hosted",
                        model_family=model_family(display_name),
                    ),
                )
                record["offers"].append(
                    {
                        "name": volc_offer_name(headings),
                        "conditions": conditions,
                        "prices": prices,
                    }
                )
        return list(grouped.values())

    def list_models(self, prefix: str = "") -> list[str]:
        models = {record["display_name"] for record in self._records()}
        if prefix:
            models = {model for model in models if model_matches(prefix, model)}
        return sorted(models, key=str.lower)

    def query(self, model: str) -> list[dict[str, Any]]:
        return [
            record
            for record in self._records()
            if model_matches(model, record["model_id"], exact=True)
            or model_matches(model, record["display_name"], exact=True)
        ]

    def search(self, model: str, *, exact: bool = False) -> list[dict[str, Any]]:
        return [
            record
            for record in self._records()
            if model_matches(model, record["model_id"], exact=exact)
            or model_matches(model, record["display_name"], exact=exact)
        ]


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
        slate: Any = state["loaderData"]["product-article"]["data"]["article"][
            "content"
        ]["slate"]
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
    spans: dict[int, list[Any]] = {}
    for raw_row in table.get("children", []):
        if raw_row.get("type") != "row":
            continue
        row: list[str | None] = []

        def consume_span(column: int) -> None:
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
            is_placeholder = cell.get("rowSpan") == 0 and cell.get("colSpan") == 0
            if is_placeholder:
                consume_span(column)
                column += 1
                continue
            while column in spans:
                consume_span(column)
                column += 1
            value = cell_text(cell)
            colspan = max(1, int(cell.get("colSpan", 1)))
            rowspan = max(1, int(cell.get("rowSpan", 1)))
            for offset in range(colspan):
                while len(row) <= column + offset:
                    row.append(None)
                row[column + offset] = value
                if rowspan > 1:
                    spans[column + offset] = [rowspan - 1, value]
            column += colspan
        for position in sorted(list(spans)):
            if position >= column:
                consume_span(position)
        grid.append([value or "" for value in row])
    return grid


class TencentAdapter(PriceSource):
    provider_id = "tencent"
    provider_name = "腾讯云 TokenHub"
    source_url = TENCENT_PRICE_URL
    source_kind = "official_document"
    catalog_url = TENCENT_LIST_URL

    def _catalog(self) -> list[dict[str, str]]:
        slate = extract_tencent_slate(self.client.get_text(TENCENT_LIST_URL))
        entries: list[dict[str, str]] = []
        for node in walk_objects(slate):
            if node.get("type") != "table":
                continue
            rows = expand_slate_table(node)
            if not rows:
                continue
            headers = [clean_text(header) for header in rows[0]]
            name_index = next(
                (i for i, h in enumerate(headers) if "模型" in h),
                None,
            )
            # The call-parameter column is labelled "model（调用参数）"; match it by
            # wording rather than by exact punctuation.
            id_index = next(
                (i for i, h in enumerate(headers) if "调用参数" in h),
                None,
            )
            if id_index is None:
                id_index = next(
                    (i for i, h in enumerate(headers) if "model" in h.lower()),
                    None,
                )
            if name_index is None or id_index is None:
                continue
            for row in rows[1:]:
                if max(name_index, id_index) >= len(row):
                    continue
                for model_id in row[id_index].splitlines():
                    model_id = model_id.strip()
                    if model_id:
                        display_name = row[name_index].strip()
                        entries.append(
                            {
                                "model_id": model_id,
                                "display_name": display_name,
                                "delivery_mode": tencent_delivery_mode(display_name),
                            }
                        )
        return entries

    def list_models(self, prefix: str = "") -> list[str]:
        models = {entry["model_id"] for entry in self._catalog()}
        if prefix:
            normalized = normalize_model(prefix)
            models = {m for m in models if normalize_model(m).startswith(normalized)}
        return sorted(models, key=str.lower)

    def _price_offers(self) -> dict[str, list[dict[str, Any]]]:
        retrieved_at = now_iso()
        slate = extract_tencent_slate(self.client.get_text(TENCENT_PRICE_URL))
        offers_by_name: dict[str, list[dict[str, Any]]] = {}
        # Prefer the 广州 region tab, but fall back to any tab that carries price
        # tables so a renamed or added region tab does not blank the provider.
        tabs = [node for node in walk_objects(slate) if node.get("type") == "tab"]
        region_tabs = [tab for tab in tabs if "广州" in str(tab.get("name", ""))] or tabs
        for node in region_tabs:
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
                    display_name = clean_text(row[name_pos])
                    if not display_name:
                        continue
                    prices = []
                    for kind in ("input", "output", "cache_hit"):
                        position = indexes[kind]
                        if (
                            position is None
                            or position >= len(row)
                            or row[position] in ("", "-")
                        ):
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
                        if (
                            position is not None
                            and position < len(row)
                            and row[position] not in ("", "-")
                        ):
                            conditions[key] = row[position]
                    if not prices:
                        continue
                    offers_by_name.setdefault(normalize_model(display_name), []).append(
                        {
                            "name": (
                                "online_standard"
                                if not conditions
                                else "online_conditional"
                            ),
                            "conditions": conditions,
                            "prices": prices,
                        }
                    )
        return offers_by_name

    def _records(self) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, str]]] = {}
        for entry in self._catalog():
            grouped.setdefault(normalize_model(entry["display_name"]), []).append(entry)
        offers_by_name = self._price_offers()
        retrieved_at = now_iso()
        records = []
        for name_key, entries in grouped.items():
            offers = offers_by_name.get(name_key, [])
            if not offers:
                continue
            aliases = sorted(
                {entry["model_id"] for entry in entries},
                key=lambda value: (len(value), value),
            )
            display_name = entries[0]["display_name"]
            records.append(
                make_record(
                    self.provider_id,
                    self.provider_name,
                    aliases[0],
                    display_name,
                    "中国区（广州）",
                    offers,
                    self.source_url,
                    self.source_kind,
                    retrieved_at,
                    catalog_url=TENCENT_LIST_URL,
                    model_aliases=aliases,
                    delivery_mode=entries[0]["delivery_mode"],
                    model_family=model_family(display_name),
                )
            )
        return records

    def query(self, model: str) -> list[dict[str, Any]]:
        query_key = normalize_model(model)
        return [
            record
            for record in self._records()
            if query_key
            in {
                normalize_model(record["display_name"]),
                *map(normalize_model, record["model_aliases"]),
            }
        ]

    def search(self, model: str, *, exact: bool = False) -> list[dict[str, Any]]:
        return [
            record
            for record in self._records()
            if model_matches(model, record["display_name"], exact=exact)
            or any(
                model_matches(model, alias, exact=exact)
                for alias in record["model_aliases"]
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
    model_header_prefix = "模型"

    def _table(self) -> list[list[str]]:
        parser = TextTableParser()
        parser.feed(self.client.get_text(DEEPSEEK_URL).replace("\x00", ""))
        table = next(
            (
                candidate
                for candidate in parser.tables
                if candidate
                and candidate[0]
                and normalize_model(candidate[0][0]).startswith(self.model_header_prefix)
            ),
            None,
        )
        if not table:
            raise SourceError("DeepSeek pricing table was not found")
        return table

    def _models(self, table: list[list[str]]) -> list[str]:
        # Model columns carry footnote markers such as "deepseek-flash(1)".
        return [strip_footnote_markers(name) for name in table[0][1:]]

    def list_models(self, prefix: str = "") -> list[str]:
        models = self._models(self._table())
        if prefix:
            normalized = normalize_model(prefix)
            models = [m for m in models if normalize_model(m).startswith(normalized)]
        return models

    def query(self, model: str) -> list[dict[str, Any]]:
        table = self._table()
        models = self._models(table)
        try:
            model_index = next(
                i
                for i, item in enumerate(models)
                if model_key(item) == model_key(model)
            )
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
            detected = next(
                (value for label, value in type_map.items() if label in joined), None
            )
            if detected:
                current_kind = detected
                current_label = next(label for label in type_map if label in joined)
            band = next((b for b in ("空闲时段", "高峰时段") if b in row), None)
            if not band or not current_kind:
                continue
            amounts = [
                re.sub(r"元$", "", value)
                for value in row
                if re.fullmatch(r"\d+(?:\.\d+)?元", value)
            ]
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
                delivery_mode="first_party",
                model_family=model_family(models[model_index]),
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
        models = {
            row[0] for _, doc in self._documents() for row in markdown_json_rows(doc)
        }
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
                    price_item(
                        "cache_hit",
                        "输入（缓存命中）",
                        amounts[0],
                        "CNY_per_million_tokens",
                    ),
                    price_item(
                        "input",
                        "输入（缓存未命中）",
                        amounts[1],
                        "CNY_per_million_tokens",
                    ),
                    price_item("output", "输出", amounts[2], "CNY_per_million_tokens"),
                ]
                return [
                    make_record(
                        self.provider_id,
                        self.provider_name,
                        row[0],
                        row[0],
                        "中国区",
                        [
                            {
                                "name": "online_standard",
                                "conditions": {},
                                "prices": prices,
                            }
                        ],
                        url,
                        self.source_kind,
                        now_iso(),
                        delivery_mode="first_party",
                        model_family=model_family(row[0]),
                    )
                ]
        return []


def parse_display_price(value: str, label: str) -> dict[str, Any]:
    cleaned = clean_text(value)
    values = numeric_values(value)
    if "免费" in cleaned:
        return price_item("other", label, "0", unit_code(value), display=cleaned)
    amount = values[-1] if values else None
    list_amount = values[0] if len(values) > 1 else None
    return price_item(
        "other",
        label,
        amount,
        unit_code(value),
        display=cleaned,
        list_amount=list_amount,
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
                        (
                            field.get("label", ""),
                            " / ".join(map(str, field.get("values", []))),
                        )
                        for field in card.get("fieldList", [])
                    ]
                    cards.append({**card, "values": values})
        return cards

    def list_models(self, prefix: str = "") -> list[str]:
        models = {
            normalize_model(card.get("title", ""))
            for card in self._cards()
            if card.get("title")
        }
        if prefix:
            normalized = normalize_model(prefix)
            models = {m for m in models if m.startswith(normalized)}
        return sorted(models)

    def query(self, model: str) -> list[dict[str, Any]]:
        card = next(
            (
                c
                for c in self._cards()
                if normalize_model(c.get("title", "")) == normalize_model(model)
            ),
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
                [
                    {
                        "name": "online_standard",
                        "conditions": conditions,
                        "prices": prices,
                    }
                ],
                self.source_url,
                self.source_kind,
                now_iso(),
                config_url=ZHIPU_CONFIG_URL,
                notes=notes,
                delivery_mode="first_party",
                model_family=model_family(card["title"]),
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
            if '<Accordion title="历史模型">' in line:
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
        matched = [
            row
            for row in self._rows()
            if normalize_model(row["model"]) == normalize_model(model)
        ]
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
                delivery_mode="first_party",
                model_family=model_family(matched[0]["model"]),
            )
        ]


def markdown_tables(text: str) -> list[tuple[str, list[list[str]]]]:
    """Return Markdown tables with their nearest preceding heading."""
    tables: list[tuple[str, list[list[str]]]] = []
    heading = ""
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if line.startswith("#"):
            heading = clean_text(line.lstrip("# "))
        if (
            line.startswith("|")
            and index + 1 < len(lines)
            and re.match(r"^\s*\|(?:\s*:?-+\s*\|)+\s*$", lines[index + 1])
        ):
            rows = [split_markdown_row(line)]
            index += 2
            while index < len(lines) and lines[index].lstrip().startswith("|"):
                rows.append(split_markdown_row(lines[index]))
                index += 1
            tables.append((heading, rows))
            continue
        index += 1
    return tables


def markdown_link_text(value: str) -> str:
    return clean_text(re.sub(r"\[([^]]+)]\([^)]+\)", r"\1", value))


def usd_amount(value: str) -> str | None:
    match = re.search(r"\$\s*(\d+(?:\.\d+)?)", value)
    return match.group(1) if match else None


def usd_price(kind: str, label: str, value: str) -> dict[str, Any] | None:
    amount = usd_amount(value)
    if not amount:
        return None
    return price_item(
        kind,
        label,
        amount,
        "USD_per_million_tokens",
        display=clean_text(value),
    )


class OpenAIAdapter(PriceSource):
    provider_id = "openai"
    provider_name = "OpenAI"
    source_url = OPENAI_URL
    source_kind = "official_markdown"

    def _rows(self) -> list[dict[str, Any]]:
        text = self.client.get_text(OPENAI_MARKDOWN_URL)
        rows: list[dict[str, Any]] = []
        for heading, table in markdown_tables(text):
            # Any "<Tier> pricing data" heading is a service tier, so a new tier
            # name does not silently drop that tier's prices.
            tier_match = re.fullmatch(r"([A-Za-z][A-Za-z-]*) pricing data", heading)
            if not tier_match or len(table) < 2:
                continue
            headers = [clean_text(cell).lower() for cell in table[0]]
            for cells in table[1:]:
                cells += [""] * (len(headers) - len(cells))
                display_name = markdown_link_text(cells[0])
                model_id = re.sub(r"\s*\([^)]*\)\s*$", "", display_name).strip()
                if not model_id:
                    continue
                offers = []
                for context in ("short", "long"):
                    prices = []
                    mappings = {
                        "input": "input",
                        "cached input": "cache_hit",
                        "cache writes": "cache_write",
                        "output": "output",
                    }
                    for suffix, kind in mappings.items():
                        label = f"{context} context {suffix}"
                        if label in headers:
                            item = usd_price(kind, label, cells[headers.index(label)])
                            if item:
                                prices.append(item)
                    if prices:
                        offers.append(
                            {
                                "name": tier_match.group(1).lower(),
                                "conditions": {
                                    "service_tier": tier_match.group(1).lower(),
                                    "context_tier": context,
                                },
                                "prices": prices,
                            }
                        )
                rows.append(
                    {
                        "model_id": model_id,
                        "display_name": display_name,
                        "offers": offers,
                    }
                )
        return rows

    def list_models(self, prefix: str = "") -> list[str]:
        models = {row["model_id"] for row in self._rows()}
        if prefix:
            key = normalize_model(prefix)
            models = {
                model for model in models if normalize_model(model).startswith(key)
            }
        return sorted(models, key=str.lower)

    def query(self, model: str) -> list[dict[str, Any]]:
        matched = [
            row
            for row in self._rows()
            if normalize_model(row["model_id"]) == normalize_model(model)
        ]
        if not matched:
            return []
        offers = [offer for row in matched for offer in row["offers"]]
        return [
            make_record(
                self.provider_id,
                self.provider_name,
                matched[0]["model_id"],
                matched[0]["display_name"],
                "全球",
                offers,
                self.source_url,
                self.source_kind,
                now_iso(),
                currency="USD",
                delivery_mode="first_party",
                model_family=model_family(matched[0]["model_id"]),
                source_api=OPENAI_MARKDOWN_URL,
            )
        ]


class AnthropicAdapter(PriceSource):
    provider_id = "anthropic"
    provider_name = "Anthropic"
    source_url = ANTHROPIC_URL
    source_kind = "official_markdown"

    def _rows(self) -> list[dict[str, Any]]:
        text = self.client.get_text(ANTHROPIC_MARKDOWN_URL)
        tables = markdown_tables(text)
        table = next(
            (rows for heading, rows in tables if heading == "Model pricing"),
            [],
        )
        if len(table) < 2:
            raise SourceError("official model pricing table was not found")
        rows = []
        kinds = ["input", "cache_write_5m", "cache_write_1h", "cache_hit", "output"]
        labels = [
            "Base input",
            "5m cache write",
            "1h cache write",
            "Cache hit",
            "Output",
        ]
        for cells in table[1:]:
            cells += [""] * (6 - len(cells))
            display_name = markdown_link_text(cells[0])
            model_id = normalize_model(re.sub(r"\s*\([^)]*\)\s*$", "", display_name))
            prices = [
                item
                for item in (
                    usd_price(kind, label, value)
                    for kind, label, value in zip(kinds, labels, cells[1:6])
                )
                if item
            ]
            # Rows are identified by pricing content, never by a name prefix, so a
            # renamed or newly branded model family is picked up without a code fix.
            if not model_id or not prices:
                continue
            conditions: dict[str, Any] = {"service_tier": "standard"}
            status = re.search(
                r"\(([^)]*(?:retired|limited availability)[^)]*)\)", display_name, re.I
            )
            if status:
                conditions["status"] = status.group(1)
            rows.append(
                {
                    "model_id": model_id,
                    "display_name": display_name,
                    "offer": {
                        "name": "standard",
                        "conditions": conditions,
                        "prices": prices,
                    },
                }
            )
        for heading, price_table in tables:
            if heading not in {"Batch processing", "Fast mode pricing"}:
                continue
            service_tier = "batch" if heading == "Batch processing" else "fast"
            for cells in price_table[1:]:
                cells += [""] * (3 - len(cells))
                names = markdown_link_text(cells[0]).split(" / ")
                for name in names:
                    model_id = normalize_model(
                        re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()
                    )
                    prices = [
                        item
                        for item in (
                            usd_price("input", "Input", cells[1]),
                            usd_price("output", "Output", cells[2]),
                        )
                        if item
                    ]
                    if not model_id or not prices:
                        continue
                    rows.append(
                        {
                            "model_id": model_id,
                            "display_name": name,
                            "offer": {
                                "name": service_tier,
                                "conditions": {"service_tier": service_tier},
                                "prices": prices,
                            },
                        }
                    )
        return rows

    def list_models(self, prefix: str = "") -> list[str]:
        models = {row["model_id"] for row in self._rows()}
        if prefix:
            key = normalize_model(prefix)
            models = {
                model for model in models if normalize_model(model).startswith(key)
            }
        return sorted(models)

    def query(self, model: str) -> list[dict[str, Any]]:
        matched = [
            row
            for row in self._rows()
            if normalize_model(row["model_id"]) == normalize_model(model)
        ]
        if not matched:
            return []
        return [
            make_record(
                self.provider_id,
                self.provider_name,
                matched[0]["model_id"],
                matched[0]["display_name"],
                "全球",
                [row["offer"] for row in matched],
                self.source_url,
                self.source_kind,
                now_iso(),
                currency="USD",
                delivery_mode="first_party",
                model_family=model_family(matched[0]["model_id"]),
                source_api=ANTHROPIC_MARKDOWN_URL,
            )
        ]


NON_MODEL_SECTION_IDS = {"notes", "pricing-for-tools", "pricing-for-agents"}


def is_model_section_id(heading_id: str) -> bool:
    """A pricing section belongs to a model unless it is an overview section.

    Matching every model slug (instead of a hard-coded family prefix) keeps new
    families such as ``gemma-4`` or ``veo-3.1`` discoverable.
    """
    if not heading_id:
        return False
    if heading_id in NON_MODEL_SECTION_IDS:
        return False
    return not heading_id.startswith("pricing-for")


class GeminiPricingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.model_id = ""
        self.display_name = ""
        self.tier = ""
        self.tables: list[dict[str, Any]] = []
        self.capture: str | None = None
        self.capture_text: list[str] = []
        self.in_table = False
        self.in_cell = False
        self.cell_text: list[str] = []
        self.row: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag in {"h2", "h3"}:
            self.capture = tag
            self.capture_text = []
            if tag == "h2":
                heading_id = str(attributes.get("id", ""))
                self.model_id = heading_id if is_model_section_id(heading_id) else ""
                self.display_name = ""
        if tag == "table" and "pricing-table" in str(attributes.get("class", "")):
            self.in_table = True
            self.rows = []
        elif self.in_table and tag == "tr":
            self.row = []
        elif self.in_table and tag in {"td", "th"}:
            self.in_cell = True
            self.cell_text = []
        elif self.in_cell and tag == "br":
            self.cell_text.append(" / ")

    def handle_data(self, data: str) -> None:
        if self.capture:
            self.capture_text.append(data)
        if self.in_cell:
            self.cell_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self.capture == tag:
            value = clean_text(" ".join(self.capture_text))
            if tag == "h2" and self.model_id:
                self.display_name = value
            elif tag == "h3":
                self.tier = value.lower()
            self.capture = None
        if self.in_table and tag in {"td", "th"}:
            self.row.append(clean_text("".join(self.cell_text)))
            self.in_cell = False
        elif self.in_table and tag == "tr" and self.row:
            self.rows.append(self.row)
        elif self.in_table and tag == "table":
            if self.model_id and self.rows:
                self.tables.append(
                    {
                        "model_id": self.model_id,
                        "display_name": self.display_name or self.model_id,
                        "tier": self.tier or "standard",
                        "rows": self.rows,
                    }
                )
            self.in_table = False


class GeminiAdapter(PriceSource):
    provider_id = "google"
    provider_name = "Google Gemini"
    source_url = GEMINI_URL
    source_kind = "official_html"

    def _tables(self) -> list[dict[str, Any]]:
        parser = GeminiPricingParser()
        parser.feed(self.client.get_text(GEMINI_URL))
        if not parser.tables:
            raise SourceError("official pricing tables were not found")
        return parser.tables

    def list_models(self, prefix: str = "") -> list[str]:
        models = {table["model_id"] for table in self._tables()}
        if prefix:
            key = normalize_model(prefix)
            models = {
                model for model in models if normalize_model(model).startswith(key)
            }
        return sorted(models)

    def query(self, model: str) -> list[dict[str, Any]]:
        tables = [
            table
            for table in self._tables()
            if normalize_model(table["model_id"]) == normalize_model(model)
        ]
        if not tables:
            return []
        offers = []
        type_map = {
            "input price": "input",
            "output price": "output",
            "context caching price": "cache_hit",
        }
        for table in tables:
            headers = [cell.lower() for cell in table["rows"][0]]
            paid_index = next(
                (index for index, cell in enumerate(headers) if "paid tier" in cell),
                None,
            )
            if paid_index is None:
                continue
            prices = []
            for row in table["rows"][1:]:
                if len(row) <= paid_index:
                    continue
                label = row[0]
                kind = next(
                    (value for key, value in type_map.items() if key in label.lower()),
                    None,
                )
                if not kind:
                    continue
                item = usd_price(kind, label, row[paid_index])
                if item:
                    prices.append(item)
                if kind == "cache_hit":
                    storage = re.search(
                        r"\$(\d+(?:\.\d+)?)\s*/\s*1,000,000 tokens per hour",
                        row[paid_index],
                    )
                    if storage:
                        prices.append(
                            price_item(
                                "cache_storage",
                                "Context cache storage",
                                storage.group(1),
                                "USD_per_million_tokens_per_hour",
                                display=row[paid_index],
                            )
                        )
            if prices:
                offers.append(
                    {
                        "name": table["tier"],
                        "conditions": {
                            "service_tier": table["tier"],
                            "billing_tier": "paid",
                        },
                        "prices": prices,
                    }
                )
        return [
            make_record(
                self.provider_id,
                self.provider_name,
                tables[0]["model_id"],
                tables[0]["display_name"],
                "全球",
                offers,
                self.source_url,
                self.source_kind,
                now_iso(),
                currency="USD",
                delivery_mode="first_party",
                model_family=model_family(tables[0]["model_id"]),
            )
        ]


def build_adapters(
    client: HttpClient,
    *,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    refresh: bool = False,
) -> dict[str, PriceSource]:
    adapters: list[PriceSource] = [
        AliyunAdapter(client),
        VolcengineAdapter(client),
        TencentAdapter(client),
        DeepSeekAdapter(client),
        KimiAdapter(client),
        ZhipuAdapter(client),
        MiniMaxAdapter(client),
        OpenAIAdapter(client),
        AnthropicAdapter(client),
        GeminiAdapter(client),
    ]
    cache = CacheStore(cache_dir)
    return {
        adapter.provider_id: CachedPriceSource(adapter, cache, refresh=refresh)
        for adapter in adapters
    }


def source_status(
    adapter: PriceSource, status: str, retrieved_at: str, error: str | None = None
) -> dict[str, Any]:
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
    cache_status = getattr(adapter, "cache_status", None)
    if cache_status and cache_status != "unused":
        item["cache"] = {
            "status": cache_status,
            "fetched_at": getattr(adapter, "cached_at", None),
        }
    return item


def inferred_overseas_providers(model: str) -> tuple[str, ...]:
    key = normalize_model(model)
    providers = []
    if re.search(r"(?:^|-)(?:openai|gpt|chatgpt|codex|sora)(?:-|$)", key) or re.match(
        r"^o\d(?:-|$)", key
    ):
        providers.append("openai")
    if re.search(r"(?:^|-)(?:anthropic|claude)(?:-|$)", key):
        providers.append("anthropic")
    if re.search(
        r"(?:^|-)(?:google|gemini|gemma|veo|lyria|imagen)(?:-|$)", key
    ):
        providers.append("google")
    return tuple(providers)


def select_compare_providers(
    adapters: dict[str, PriceSource],
    model: str,
    *,
    requested: list[str] | None = None,
    include_overseas: bool = False,
) -> list[PriceSource]:
    if requested:
        provider_ids = requested
    else:
        overseas = (
            OVERSEAS_PROVIDER_IDS
            if include_overseas
            else inferred_overseas_providers(model)
        )
        provider_ids = [*DOMESTIC_PROVIDER_IDS, *overseas]
    return [adapters[provider_id] for provider_id in dict.fromkeys(provider_ids)]


def query_adapters(
    adapters: Iterable[PriceSource], model: str, *, exact: bool = False
) -> dict[str, Any]:
    started = now_iso()
    records = []
    checks = []
    for adapter in adapters:
        checked_at = now_iso()
        try:
            found = adapter.search(model, exact=exact)
            records.extend(found)
            checks.append(
                source_status(
                    adapter, "available" if found else "not_found", checked_at
                )
            )
        except Exception as exc:  # keep other providers usable when one source changes
            checks.append(source_status(adapter, "source_error", checked_at, str(exc)))
    return {
        "query": model,
        "match_mode": "exact" if exact else "model_family",
        "retrieved_at": started,
        "results": records,
        "source_checks": checks,
    }


def price_lookup(offer: dict[str, Any], kind: str) -> dict[str, Any] | None:
    return next(
        (item for item in offer.get("prices", []) if item.get("type") == kind), None
    )


def format_price(item: dict[str, Any] | None) -> str:
    if not item:
        return "—"
    display = item.get("display")
    amount = item.get("amount")
    unit = item.get("unit")
    if display and (
        "免费" in display
        or len(re.findall(r"\$\s*\d", display)) > 1
        or re.search(r"\b(?:through|starting)\b", display, re.I)
    ):
        return display
    if amount is None:
        return display or "—"
    unit_labels = {
        "CNY_per_million_tokens": "元/百万 tokens",
        "CNY_per_million_tokens_per_hour": "元/百万 tokens/小时",
        "CNY_per_10k_characters": "元/万字符",
        "CNY_per_request": "元/次",
        "USD_per_million_tokens": "美元/百万 tokens",
        "USD_per_million_tokens_per_hour": "美元/百万 tokens/小时",
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
        discount = (
            f"，discount={item['discount']}" if item.get("discount") is not None else ""
        )
        items.append(
            f"{item.get('label', item.get('type', '价格'))}: {value}{discount}"
        )
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
                "| 厂商 | 模型/版本 | 服务方式 | 地域 | 计费条件 | 输入 | 输出 | 缓存命中 | 缓存写入/存储 | 其他价格 | 来源 |",
                "|---|---|---|---|---|---:|---:|---:|---:|---|---|",
            ]
        )
        delivery_labels = {
            "platform_hosted": "平台托管",
            "self_deployed": "自部署",
            "upstream_direct": "原厂直供",
            "third_party_hosted": "第三方托管",
            "first_party": "原厂",
        }
        rows_written = 0
        for record in results:
            for offer in record.get("offers", []):
                conditions = offer.get("conditions", {})
                details = [offer.get("name", "标准")]
                details.extend(f"{k}={v}" for k, v in conditions.items())
                condition_text = "；".join(filter(None, details))
                source = record["source"]["url"]
                lines.append(
                    "| {provider} | {model} | {delivery} | {region} | {conditions} | {input} | {output} | {cache} | {storage} | {other} | [官方来源]({source}) |".format(
                        provider=record["provider"]["name"],
                        model=f"{record.get('display_name', record['model_id'])} (`{record['model_id']}`)",
                        delivery=delivery_labels.get(
                            record.get("delivery_mode"),
                            record.get("delivery_mode", "—"),
                        ),
                        region=record["region"],
                        conditions=condition_text.replace("|", "\\|"),
                        input=format_price(price_lookup(offer, "input")),
                        output=format_price(price_lookup(offer, "output")),
                        cache=format_price(price_lookup(offer, "cache_hit")),
                        storage=format_price(
                            price_lookup(offer, "cache_write")
                            or price_lookup(offer, "cache_storage")
                        ),
                        other=format_other_prices(offer).replace("|", "\\|"),
                        source=source,
                    )
                )
                rows_written += 1
        if not rows_written:
            lines.append(
                "匹配到的模型存在，但官方文档未给出本工具可解析的价格"
                "（例如按秒/按次计费或仅在免费档提供）。"
            )
    else:
        lines.append("没有来源确认提供匹配的模型或版本。")
    lines.extend(["", "## 来源检查", ""])
    labels = {
        "available": "已找到",
        "not_found": "未找到匹配模型",
        "source_error": "来源解析失败",
    }
    for check in payload.get("source_checks", []):
        source = check["source"]
        error = f"；{check['error']}" if check.get("error") else ""
        cache = check.get("cache")
        cache_text = (
            f"；缓存 {cache['status']}，最后拉取 {cache.get('fetched_at') or '未知'}"
            if cache
            else ""
        )
        lines.append(
            f"- {check['provider']['name']}：{labels.get(check['status'], check['status'])}；"
            f"[{source['kind']}]({source['url']})；检查时间 {source['retrieved_at']}"
            f"{cache_text}{error}"
        )
    return "\n".join(lines) + "\n"


def emit(payload: Any, output_format: str) -> None:
    if output_format == "markdown":
        print(to_markdown(payload), end="")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Query official model catalogs and prices with sources"
    )
    parser.add_argument(
        "--timeout", type=int, default=30, help="HTTP timeout in seconds"
    )
    parser.add_argument(
        "--cache-dir", type=Path, default=DEFAULT_CACHE_DIR, help=argparse.SUPPRESS
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    compare = subparsers.add_parser(
        "compare", help="compare a model family across all providers"
    )
    compare.add_argument("model")
    compare.add_argument(
        "--exact", action="store_true", help="disable model-family matching"
    )
    compare.add_argument(
        "--provider",
        action="append",
        dest="providers",
        help="query only this provider; repeat to compare selected providers",
    )
    compare.add_argument(
        "--include-overseas",
        action="store_true",
        help="also query OpenAI, Anthropic, and Google",
    )
    compare.add_argument(
        "--refresh",
        action="store_true",
        help="ignore fresh caches for selected providers",
    )
    compare.add_argument("--format", choices=("json", "markdown"), default="json")

    provider = subparsers.add_parser(
        "provider", help="query a model family from one provider"
    )
    provider.add_argument("provider")
    provider.add_argument("model")
    provider.add_argument(
        "--exact", action="store_true", help="disable model-family matching"
    )
    provider.add_argument("--refresh", action="store_true", help="ignore fresh cache")
    provider.add_argument("--format", choices=("json", "markdown"), default="json")

    listing = subparsers.add_parser("list", help="list model IDs from one provider")
    listing.add_argument("provider")
    listing.add_argument("--prefix", default="")
    listing.add_argument("--refresh", action="store_true", help="ignore fresh cache")
    listing.add_argument("--format", choices=("json",), default="json")

    args = parser.parse_args()
    adapters = build_adapters(
        HttpClient(args.timeout), cache_dir=args.cache_dir, refresh=args.refresh
    )
    selected_provider = getattr(args, "provider", None)
    if selected_provider is not None and selected_provider not in adapters:
        parser.error(
            f"unknown provider: {selected_provider}; choose from {', '.join(adapters)}"
        )
    requested = getattr(args, "providers", None)
    invalid = [
        provider_id for provider_id in requested or [] if provider_id not in adapters
    ]
    if invalid:
        parser.error(
            f"unknown provider: {', '.join(invalid)}; choose from {', '.join(adapters)}"
        )

    if args.command == "compare":
        selected = select_compare_providers(
            adapters,
            args.model,
            requested=args.providers,
            include_overseas=args.include_overseas,
        )
        emit(query_adapters(selected, args.model, exact=args.exact), args.format)
    elif args.command == "provider":
        emit(
            query_adapters([adapters[args.provider]], args.model, exact=args.exact),
            args.format,
        )
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
            cache_status = getattr(adapter, "cache_status", None)
            if cache_status and cache_status != "unused":
                payload["cache"] = {
                    "status": cache_status,
                    "fetched_at": getattr(adapter, "cached_at", None),
                }
        except Exception as exc:
            payload = source_status(adapter, "source_error", checked_at, str(exc))
        emit(payload, args.format)
    return 0


if __name__ == "__main__":
    sys.exit(main())
