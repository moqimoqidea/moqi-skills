import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "query_model_prices.py"
SPEC = importlib.util.spec_from_file_location("query_model_prices", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


def text_zone(value):
    return {
        "ops": [
            {"insert": "*", "attributes": {"lineId": "marker"}},
            {"insert": f"{value}\n"},
        ]
    }


def volc_payload():
    rows = ["header", "model"]
    columns = ["name", "input", "output"]
    data = {
        "0": {
            "ops": [
                {"insert": "*", "attributes": {"heading": "h1"}},
                {"insert": "大语言模型\n"},
                {"insert": "*", "attributes": {"heading": "h2"}},
                {"insert": "在线推理（常规）\n"},
                {"insert": "*", "attributes": {"aceTable": "rows columns"}},
            ]
        },
        "rows": {"ops": [{"insert": {"id": row}} for row in rows]},
        "columns": {"ops": [{"insert": {"id": column}} for column in columns]},
    }
    values = [
        ["模型名称", "输入 元/百万token", "输出 元/百万token"],
        ["deepseek-v4-pro正式版", "9.00", "27.00"],
    ]
    for row_id, row in zip(rows, values):
        for column_id, value in zip(columns, row):
            data[f"x{row_id}x{column_id}"] = text_zone(value)
    return {
        "Result": {
            "ContentType": "json",
            "Content": json.dumps({"version": "test", "data": data}),
            "UpdatedTime": "2026-09-09T00:00:00Z",
        }
    }


class FakeClient:
    def get_text(self, url):
        return json.dumps(volc_payload())


class MappingClient:
    def __init__(self, values):
        self.values = values

    def get_text(self, url):
        value = self.values[url]
        if isinstance(value, Exception):
            raise value
        return value


class CountingSource(MODULE.PriceSource):
    provider_id = "counting"
    provider_name = "Counting"
    source_url = "https://example.test/pricing"
    source_kind = "test"

    def __init__(self):
        self.calls = 0

    def list_models(self, prefix=""):
        self.calls += 1
        return ["model-a"]

    def query(self, model):
        self.calls += 1
        return [{"model_id": model}]


class FailingSource(CountingSource):
    def query(self, model):
        raise MODULE.SourceError("offline")


class ModelMatchingTests(unittest.TestCase):
    def test_family_match_includes_versions_and_labels(self):
        self.assertTrue(MODULE.model_matches("deepseek-v4-pro", "deepseek-v4-pro-0813"))
        self.assertTrue(
            MODULE.model_matches("deepseek-v4-pro", "deepseek-v4-pro正式版")
        )
        self.assertTrue(
            MODULE.model_matches("deepseek-v4-pro", "vanchin/deepseek-v4-pro")
        )
        self.assertFalse(MODULE.model_matches("deepseek-v4-pro", "deepseek-v4-flash"))

    def test_exact_match_does_not_expand_family(self):
        self.assertFalse(
            MODULE.model_matches("deepseek-v4-pro", "deepseek-v4-pro-0813", exact=True)
        )

    def test_tencent_delivery_modes_are_distinct(self):
        self.assertEqual(
            MODULE.tencent_delivery_mode("DeepSeek-V4-Pro 原厂直供"),
            "upstream_direct",
        )
        self.assertEqual(
            MODULE.tencent_delivery_mode("DeepSeek-V4-Pro"), "self_deployed"
        )


class StructuredDocumentTests(unittest.TestCase):
    def test_volcengine_reads_catalog_and_prices_from_document_json(self):
        records = MODULE.VolcengineAdapter(FakeClient()).search("deepseek-v4-pro")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["display_name"], "deepseek-v4-pro正式版")
        self.assertEqual(records[0]["source"]["url"], MODULE.VOLCENGINE_PAGE_URL)
        self.assertEqual(records[0]["source_api"], MODULE.VOLCENGINE_DOC_API)
        self.assertEqual(records[0]["offers"][0]["prices"][0]["amount"], "9.00")

    def test_tencent_rowspan_placeholders_keep_columns_aligned(self):
        def cell(value, row_span=None, col_span=None):
            node = {
                "type": "cell",
                "children": [{"type": "p", "children": [{"text": value}]}],
            }
            if row_span is not None:
                node["rowSpan"] = row_span
            if col_span is not None:
                node["colSpan"] = col_span
            return node

        table = {
            "children": [
                {"type": "row", "children": [cell("模型"), cell("峰谷"), cell("输入")]},
                {
                    "type": "row",
                    "children": [cell("model-a", 2), cell("空闲"), cell("1")],
                },
                {"type": "row", "children": [cell("", 0, 0), cell("高峰"), cell("2")]},
            ]
        }
        self.assertEqual(
            MODULE.expand_slate_table(table),
            [
                ["模型", "峰谷", "输入"],
                ["model-a", "空闲", "1"],
                ["model-a", "高峰", "2"],
            ],
        )


class CacheTests(unittest.TestCase):
    def test_cache_is_provider_scoped_and_expires_after_three_hours(self):
        current = [datetime(2026, 9, 9, 0, 0, tzinfo=timezone.utc)]
        with tempfile.TemporaryDirectory() as directory:
            cache = MODULE.CacheStore(Path(directory), clock=lambda: current[0])
            source = CountingSource()
            adapter = MODULE.CachedPriceSource(source, cache)

            self.assertEqual(adapter.list_models(), ["model-a"])
            self.assertEqual(adapter.cache_status, "miss")
            self.assertEqual(adapter.list_models(), ["model-a"])
            self.assertEqual(adapter.cache_status, "hit")
            self.assertEqual(source.calls, 1)

            other_source = CountingSource()
            other_source.provider_id = "other"
            other = MODULE.CachedPriceSource(other_source, cache)
            self.assertEqual(other.list_models(), ["model-a"])
            self.assertEqual(other.cache_status, "miss")
            self.assertEqual(other_source.calls, 1)

            current[0] += timedelta(hours=3, seconds=1)
            self.assertEqual(adapter.list_models(), ["model-a"])
            self.assertEqual(adapter.cache_status, "miss")
            self.assertEqual(source.calls, 2)

    def test_refresh_bypasses_a_fresh_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = MODULE.CacheStore(Path(directory))
            source = CountingSource()
            MODULE.CachedPriceSource(source, cache).query("model-a")
            refreshed = MODULE.CachedPriceSource(source, cache, refresh=True)
            refreshed.query("model-a")
            self.assertEqual(refreshed.cache_status, "refreshed")
            self.assertEqual(source.calls, 2)

    def test_expired_cache_does_not_hide_a_refresh_failure(self):
        current = [datetime(2026, 9, 9, 0, 0, tzinfo=timezone.utc)]
        with tempfile.TemporaryDirectory() as directory:
            cache = MODULE.CacheStore(Path(directory), clock=lambda: current[0])
            MODULE.CachedPriceSource(CountingSource(), cache).query("model-a")
            current[0] += timedelta(hours=3, seconds=1)
            adapter = MODULE.CachedPriceSource(FailingSource(), cache)
            with self.assertRaises(MODULE.SourceError):
                adapter.query("model-a")
            self.assertEqual(adapter.cache_status, "refresh_failed")


class OverseasRoutingTests(unittest.TestCase):
    def test_domestic_query_does_not_select_overseas_sources(self):
        adapters = {
            provider: object()
            for provider in (
                *MODULE.DOMESTIC_PROVIDER_IDS,
                *MODULE.OVERSEAS_PROVIDER_IDS,
            )
        }
        selected = MODULE.select_compare_providers(adapters, "deepseek-v4-pro")
        self.assertEqual(len(selected), len(MODULE.DOMESTIC_PROVIDER_IDS))
        self.assertNotIn(adapters["openai"], selected)

    def test_model_name_selects_only_its_relevant_overseas_provider(self):
        self.assertEqual(MODULE.inferred_overseas_providers("gpt-5"), ("openai",))
        self.assertEqual(
            MODULE.inferred_overseas_providers("claude-sonnet-5"), ("anthropic",)
        )
        self.assertEqual(
            MODULE.inferred_overseas_providers("gemini-2.5-pro"), ("google",)
        )

    def test_unavailable_overseas_source_is_reported_as_source_error(self):
        adapter = MODULE.OpenAIAdapter(
            MappingClient({MODULE.OPENAI_MARKDOWN_URL: MODULE.SourceError("blocked")})
        )
        payload = MODULE.query_adapters([adapter], "gpt-5")
        self.assertEqual(payload["source_checks"][0]["status"], "source_error")


class OverseasParserTests(unittest.TestCase):
    def test_openai_markdown_parser(self):
        markdown = """### Standard pricing data
| Model | Short context input | Short context cached input | Short context cache writes | Short context output | Long context input | Long context cached input | Long context cache writes | Long context output |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| gpt-test | $1.00 | $0.10 | - | $4.00 | $2.00 | $0.20 | - | $6.00 |
"""
        adapter = MODULE.OpenAIAdapter(
            MappingClient({MODULE.OPENAI_MARKDOWN_URL: markdown})
        )
        record = adapter.query("gpt-test")[0]
        self.assertEqual(record["currency"], "USD")
        self.assertEqual(len(record["offers"]), 2)
        self.assertEqual(record["offers"][0]["prices"][0]["amount"], "1.00")

    def test_anthropic_markdown_parser(self):
        markdown = """## Model pricing
| Model | Base input tokens | 5m cache writes | 1h cache writes | Cache hits and refreshes | Output tokens |
| --- | --- | --- | --- | --- | --- |
| Claude Test 1 | $2 / MTok | $2.50 / MTok | $4 / MTok | $0.20 / MTok | $10 / MTok |
"""
        adapter = MODULE.AnthropicAdapter(
            MappingClient({MODULE.ANTHROPIC_MARKDOWN_URL: markdown})
        )
        record = adapter.query("claude-test-1")[0]
        self.assertEqual(record["offers"][0]["prices"][-1]["amount"], "10")

    def test_gemini_html_parser_uses_paid_tier(self):
        html = """<h2 id="gemini-test">Gemini Test</h2><code>gemini-test</code>
<section><h3>Standard</h3><table class="pricing-table">
<tr><th></th><th>Free Tier</th><th>Paid Tier, per 1M tokens in USD</th></tr>
<tr><td>Input price</td><td>Free</td><td>$0.50</td></tr>
<tr><td>Output price</td><td>Free</td><td>$2.00</td></tr>
</table></section>"""
        adapter = MODULE.GeminiAdapter(MappingClient({MODULE.GEMINI_URL: html}))
        record = adapter.query("gemini-test")[0]
        self.assertEqual(record["offers"][0]["conditions"]["billing_tier"], "paid")
        self.assertEqual(record["offers"][0]["prices"][0]["amount"], "0.50")


DEEPSEEK_HTML = """
<table>
<tr><th>模型</th><th>deepseek-flash(1)</th><th>deepseek-v4-pro(2)</th></tr>
<tr><td>模型版本</td><td>DeepSeek-V4.1-Flash</td><td>DeepSeek-V4-Pro-0813</td></tr>
<tr><td>价格(3)</td><td>百万tokens输入 （缓存命中）</td><td>空闲时段</td><td>0.02元</td><td>0.15元</td></tr>
<tr><td>高峰时段</td><td>0.04元</td><td>0.30元</td></tr>
<tr><td>百万tokens输入 （缓存未命中）</td><td>空闲时段</td><td>1元</td><td>4.5元</td></tr>
<tr><td>高峰时段</td><td>2元</td><td>9.0元</td></tr>
<tr><td>百万tokens输出</td><td>空闲时段</td><td>4元</td><td>13.5元</td></tr>
<tr><td>高峰时段</td><td>8元</td><td>27.0元</td></tr>
</table>
"""


class DeepSeekAdapterTests(unittest.TestCase):
    def adapter(self):
        return MODULE.DeepSeekAdapter(
            MappingClient({MODULE.DEEPSEEK_URL: DEEPSEEK_HTML})
        )

    def test_footnote_markers_do_not_break_the_catalog(self):
        self.assertEqual(
            self.adapter().list_models(), ["deepseek-flash", "deepseek-v4-pro"]
        )

    def test_prices_are_read_for_each_time_band(self):
        record = self.adapter().query("deepseek-flash")[0]
        self.assertEqual(record["model_id"], "deepseek-flash")
        self.assertEqual(len(record["offers"]), 2)
        off_peak = MODULE.price_lookup(record["offers"][0], "input")
        self.assertEqual(off_peak["amount"], "1")
        self.assertEqual(
            MODULE.price_lookup(record["offers"][0], "cache_hit")["amount"], "0.02"
        )
        self.assertEqual(
            MODULE.price_lookup(record["offers"][1], "output")["amount"], "8"
        )

    def test_retired_name_still_resolves_through_search(self):
        records = self.adapter().search("deepseek-v4-flash")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["model_id"], "deepseek-flash")


class ModelNameCouplingTests(unittest.TestCase):
    def test_footnote_markers_are_not_part_of_model_identity(self):
        self.assertEqual(
            MODULE.strip_footnote_markers("deepseek-flash(1)"), "deepseek-flash"
        )
        self.assertEqual(
            MODULE.strip_footnote_markers("deepseek-v4-pro（2）"), "deepseek-v4-pro"
        )
        self.assertEqual(MODULE.normalize_model("deepseek-flash(1)"), "deepseek-flash")

    def test_retired_alias_adds_matches_without_losing_family_expansion(self):
        self.assertTrue(MODULE.model_matches("deepseek-v4-flash", "deepseek-flash"))
        self.assertTrue(
            MODULE.model_matches("deepseek-v4-flash", "deepseek-v4-flash-0731")
        )
        self.assertFalse(MODULE.model_matches("deepseek-v4-flash", "deepseek-v4-pro"))

    def test_anthropic_does_not_filter_rows_by_name_prefix(self):
        markdown = """## Model pricing
| Model | Base input tokens | 5m cache writes | 1h cache writes | Cache hits and refreshes | Output tokens |
| --- | --- | --- | --- | --- | --- |
| Anthropic Nova 1 | $3 / MTok | $3.75 / MTok | $6 / MTok | $0.30 / MTok | $15 / MTok |
"""
        adapter = MODULE.AnthropicAdapter(
            MappingClient({MODULE.ANTHROPIC_MARKDOWN_URL: markdown})
        )
        self.assertEqual(adapter.list_models(), ["anthropic-nova-1"])
        record = adapter.query("anthropic-nova-1")[0]
        self.assertEqual(record["offers"][0]["prices"][0]["amount"], "3")

    def test_gemini_keeps_other_google_families_and_drops_overview_sections(self):
        html = """<h2 id="gemma-4">Gemma 4</h2>
<section><h3>Standard</h3><table class="pricing-table">
<tr><th></th><th>Free Tier</th><th>Paid Tier, per 1M tokens in USD</th></tr>
<tr><td>Input price</td><td>Free</td><td>$0.10</td></tr>
</table></section>
<h2 id="pricing-for-tools">Pricing for tools</h2>
<section><h3>Standard</h3><table class="pricing-table">
<tr><th></th><th>Free Tier</th><th>Paid Tier, per 1M tokens in USD</th></tr>
<tr><td>Input price</td><td>Free</td><td>$9.99</td></tr>
</table></section>"""
        adapter = MODULE.GeminiAdapter(MappingClient({MODULE.GEMINI_URL: html}))
        self.assertEqual(adapter.list_models(), ["gemma-4"])
        record = adapter.query("gemma-4")[0]
        self.assertEqual(record["offers"][0]["prices"][0]["amount"], "0.10")
        self.assertEqual(adapter.query("pricing-for-tools"), [])

    def test_google_family_names_route_to_the_google_source(self):
        self.assertEqual(MODULE.inferred_overseas_providers("gemma-4"), ("google",))
        self.assertEqual(MODULE.inferred_overseas_providers("veo-3.1"), ("google",))

    def test_openai_accepts_any_pricing_tier_heading(self):
        markdown = """### Priority pricing data
| Model | Short context input | Short context cached input | Short context cache writes | Short context output | Long context input | Long context cached input | Long context cache writes | Long context output |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| gpt-test | $2.00 | $0.20 | - | $8.00 | $4.00 | $0.40 | - | $12.00 |
"""
        adapter = MODULE.OpenAIAdapter(
            MappingClient({MODULE.OPENAI_MARKDOWN_URL: markdown})
        )
        record = adapter.query("gpt-test")[0]
        self.assertEqual(record["offers"][0]["name"], "priority")


class MarkdownRenderingTests(unittest.TestCase):
    def test_matched_model_without_parseable_prices_is_explained(self):
        payload = {
            "query": "veo-3.1",
            "retrieved_at": "2026-09-11T00:00:00+08:00",
            "results": [
                {
                    "provider": {"id": "google", "name": "Google Gemini"},
                    "model_id": "veo-3.1",
                    "display_name": "Veo 3.1",
                    "region": "全球",
                    "offers": [],
                    "source": {
                        "url": "https://example.test/pricing",
                        "kind": "official_html",
                        "retrieved_at": "2026-09-11T00:00:00+08:00",
                    },
                }
            ],
            "source_checks": [],
        }
        self.assertIn("未给出本工具可解析的价格", MODULE.to_markdown(payload))


if __name__ == "__main__":
    unittest.main()
