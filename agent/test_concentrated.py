# -*- coding: utf-8 -*-
"""2026-11-17 集中返修回归测试（默认不调用真实模型、不写数据库）。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.nodes.response import _decide_response_type
from agent.nodes.schema_retrieval import _extract_keywords
from agent.nodes.sql_execution import sql_execution_node
from agent.nodes.sql_generation import _extract_sql, sql_generation_node
from agent.sql_guard import SqlGuardError, prepare_readonly_query


class SqlExtractionTests(unittest.TestCase):
    def test_only_outer_markdown_fence_is_removed(self):
        quoted = "SELECT total_gmv FROM `datamind_ads`.`ads_gmv_overview`"
        cases = [
            quoted,
            quoted + ";",
            "```sql\n" + quoted + "\n```",
            "```SQL\n" + quoted + ";\n```",
            "```\nSELECT 'a;b' AS note, '`tick`' AS quoted;\n```",
        ]
        expected = [
            quoted,
            quoted + ";",
            quoted,
            quoted + ";",
            "SELECT 'a;b' AS note, '`tick`' AS quoted;",
        ]
        for raw, want in zip(cases, expected):
            with self.subTest(raw=raw):
                self.assertEqual(_extract_sql(raw), want)

    def test_quoted_identifier_generation_then_guard(self):
        raw = "```sql\nSELECT total_gmv FROM `datamind_ads`.`ads_gmv_overview`\n```"
        with patch("agent.nodes.sql_generation.chat", return_value=raw):
            state = sql_generation_node({
                "user_question": "总GMV", "chat_history": [], "schema_context": "表名: ads_gmv_overview (数据库: datamind_ads)",
                "retry_count": 0,
            })
        prepared, _ = prepare_readonly_query(state["sql"])
        self.assertIn("`ads_gmv_overview`", prepared)


class SqlGuardScopeTests(unittest.TestCase):
    DENIED = [
        "WITH ods_customers AS (SELECT 1 AS x) SELECT * FROM datamind_ods.ods_customers",
        "SELECT * FROM (SELECT total_gmv FROM ads_gmv_overview FOR UPDATE) t",
        "SELECT * FROM ads_gmv_overview UNION SELECT * FROM (SELECT total_gmv FROM ads_gmv_overview LOCK IN SHARE MODE) t",
        "WITH a AS (SELECT * FROM datamind_ods.ods_customers) SELECT * FROM a",
    ]

    def test_scope_and_nested_locks_denied_before_connection(self):
        for sql in self.DENIED:
            with self.subTest(sql=sql), patch("agent.nodes.sql_execution.pymysql.connect") as connect:
                result = sql_execution_node({"sql": sql, "schema_context": "", "retry_count": 0})
                self.assertEqual(result["sql_error_type"], "guard")
                connect.assert_not_called()

    def test_valid_cte_subquery_union_and_cross_db_remain_available(self):
        valid = [
            "WITH a AS (SELECT total_gmv FROM ads_gmv_overview) SELECT * FROM a",
            "WITH a AS (SELECT 1 AS x), b AS (SELECT * FROM a) SELECT * FROM b",
            "SELECT * FROM (SELECT total_gmv FROM ads_gmv_overview LIMIT 1) t",
            "SELECT category_english FROM ads_category_rank UNION SELECT category_english FROM dws_category_summary",
        ]
        for sql in valid:
            with self.subTest(sql=sql):
                prepared, meta = prepare_readonly_query(sql)
                self.assertTrue(prepared)
                self.assertIn("limit_action", meta)


class FakeResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body or {}

    def json(self):
        return self._body


class FakeClient:
    responses = []
    requests = []

    def __init__(self, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def get(self, url, params=None):
        self.requests.append((url, params))
        item = self.responses[len(self.requests) - 1]
        if isinstance(item, Exception):
            raise item
        return item


class SchemaRetrievalTests(unittest.TestCase):
    def test_keywords_are_stable_and_semantic_priority(self):
        self.assertEqual(_extract_keywords("总用户数是多少？")[0], "total_customers")
        self.assertEqual(_extract_keywords("每日下单用户数趋势")[0], "active_user_count")
        self.assertEqual(_extract_keywords("总用户数是多少？"), _extract_keywords("总用户数是多少？"))
        self.assertIn("rank", _extract_keywords("那前三名呢"))

    def test_partial_failure_and_empty_success_are_distinct(self):
        from agent.nodes.schema_retrieval import schema_retrieval_node
        FakeClient.requests = []
        FakeClient.responses = [
            FakeResponse(body={"code": 200, "data": []}),
            FakeResponse(body={"code": 500, "message": "synthetic failure", "data": None}),
        ]
        with patch("agent.nodes.schema_retrieval._extract_keywords", return_value=["empty", "broken"]), \
             patch("agent.nodes.schema_retrieval.httpx.Client", FakeClient):
            result = schema_retrieval_node({"user_question": "测试", "chat_history": []})
        self.assertEqual(result["schema_status"], "partial")
        self.assertIn("未找到", result["schema_context"])

    def test_http_200_business_failure_is_not_success(self):
        from agent.nodes.schema_retrieval import SchemaRetrievalError, schema_retrieval_node
        FakeClient.requests = []
        FakeClient.responses = [FakeResponse(body={"code": 500, "message": "synthetic", "data": None})]
        with patch("agent.nodes.schema_retrieval._extract_keywords", return_value=["x"]), \
             patch("agent.nodes.schema_retrieval.httpx.Client", FakeClient):
            with self.assertRaises(SchemaRetrievalError):
                schema_retrieval_node({"user_question": "测试", "chat_history": []})


class SemanticAndChartTests(unittest.TestCase):
    def test_period_distinct_users_is_unsupported_without_model_or_database(self):
        with patch("agent.nodes.sql_generation.chat", side_effect=AssertionError("不应调用模型")), \
             patch("agent.nodes.sql_execution.pymysql.connect") as connect:
            state = sql_generation_node({
                "user_question": "2017年去重用户数是多少？", "chat_history": [],
                "schema_context": "表名: dws_daily_active_users (数据库: datamind_dws)", "retry_count": 0,
            })
            result = sql_execution_node(state)
        self.assertEqual(result["sql_error_type"], "unsupported")
        connect.assert_not_called()

    def test_placeholder_sql_is_not_business_result(self):
        with patch("agent.nodes.sql_generation.chat", return_value="SELECT '未找到相关数据' AS message"):
            state = sql_generation_node({
                "user_question": "未知指标", "chat_history": [], "schema_context": "未找到相关数据表。", "retry_count": 0,
            })
        self.assertEqual(state["sql_error_type"], "unsupported")
        self.assertIsNone(state["sql"])

    def test_top3_uses_single_metric_bar_not_partial_pie(self):
        rows = [
            {"rank": 1, "category_english": "health_beauty", "total_gmv": 1437665.78, "total_orders": 8800, "gmv_ratio": 9.14},
            {"rank": 2, "category_english": "watches_gifts", "total_gmv": 1298292.47, "total_orders": 5604, "gmv_ratio": 8.25},
            {"rank": 3, "category_english": "bed_bath_table", "total_gmv": 1240386.13, "total_orders": 9399, "gmv_ratio": 7.88},
        ]
        rtype, cfg = _decide_response_type("那前三名呢？", rows)
        self.assertEqual((rtype, cfg["type"]), ("chart", "bar"))
        self.assertEqual(cfg["y"], ["total_gmv"])
        self.assertEqual(cfg["unit"], "BRL")
        self.assertAlmostEqual(sum(r["gmv_ratio"] for r in rows), 25.27)

    def test_seller_chart_does_not_mix_brl_and_orders(self):
        rows = [{"rank": 1, "seller_id": "s1", "total_gmv": 100.0, "total_orders": 99}]
        _, cfg = _decide_response_type("卖家排行前五", rows)
        self.assertEqual(cfg["y"], ["total_gmv"])
        self.assertEqual(cfg["unit"], "BRL")

    def test_full_global_ratio_pie_declares_denominator(self):
        rows = [{"category_english": f"c{i}", "gmv_ratio": 100 / 72} for i in range(72)]
        sql = "SELECT category_english, gmv_ratio FROM datamind_ads.ads_category_rank"
        specs = __import__("agent.result_semantics", fromlist=["output_specs"]).output_specs(sql, list(rows[0]))
        _, cfg = _decide_response_type("各品类全局GMV占比分布", rows, specs, category_count=72)
        self.assertEqual(cfg["type"], "pie")
        self.assertEqual(cfg["denominator"], "全局GMV")
        self.assertEqual(cfg["coverage"], "complete")


class ConfigurationAndRuntimeTests(unittest.TestCase):
    def test_sqlalchemy_url_special_character_roundtrip(self):
        from sqlalchemy.engine import make_url
        from backend.db.connection import build_mysql_url
        synthetic = "synthetic@:#/pw?[]"
        url = build_mysql_url("datamind_ads", username="synthetic_user", password=synthetic)
        parsed = make_url(url.render_as_string(hide_password=False))
        self.assertEqual(parsed.password, synthetic)
        self.assertEqual(parsed.username, "synthetic_user")

    def test_llm_client_has_bounded_timeout_and_retry(self):
        import agent.llm as llm
        captured = {}
        class FakeOpenAI:
            def __init__(self, **kwargs):
                captured.update(kwargs)
        with patch.object(llm, "_client", None), patch.object(llm, "OpenAI", FakeOpenAI), \
             patch.object(llm, "require_api_key", return_value="synthetic-model-key"):
            llm.get_client()
        self.assertLessEqual(captured["max_retries"], 1)
        self.assertLessEqual(captured["timeout"].connect, 10)
        self.assertLessEqual(captured["timeout"].read, 60)

    def test_llm_length_finish_is_not_accepted_as_complete(self):
        import agent.llm as llm
        response = SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content="不完整总结"), finish_reason="length"
        )])
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: response)))
        with patch.object(llm, "get_client", return_value=client):
            with self.assertRaises(llm.LLMOutputError):
                llm.chat([{"role": "user", "content": "test"}])

    def test_readonly_session_configuration_failure_is_not_ignored(self):
        class Cursor:
            def __init__(self): self.calls = []
            def __enter__(self): return self
            def __exit__(self, *args): return None
            def execute(self, sql, *args):
                self.calls.append(sql)
                if sql.startswith("SET SESSION"):
                    import pymysql
                    raise pymysql.err.OperationalError(1193, "synthetic unsupported")
        class Conn:
            def __enter__(self): return self
            def __exit__(self, *args): return None
            def cursor(self): return Cursor()
        with patch("agent.nodes.sql_execution.pymysql.connect", return_value=Conn()):
            result = sql_execution_node({"sql": "SELECT total_gmv FROM ads_gmv_overview", "schema_context": "", "retry_count": 0})
        self.assertEqual(result["sql_error_type"], "configuration")


if __name__ == "__main__":
    unittest.main(verbosity=2)
