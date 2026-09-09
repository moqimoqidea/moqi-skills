import importlib.util
import json
import unittest
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


if __name__ == "__main__":
    unittest.main()
