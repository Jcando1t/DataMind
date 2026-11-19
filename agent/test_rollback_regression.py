# -*- coding: utf-8 -*-
"""集中返修回退轮新增回归测试（不调用真实模型、不写业务数据）。

针对独立验收报告新发现/回退的缺陷补充：
1. G 项：已配置的短秘密（<6 字符）同样必须被脱敏，不得因长度门槛忽略。
2. G 项：answer / SQL / rows / chart_config / 嵌套结构全部递归脱敏。
3. P1：config 与 llm 接口一致（LLM 超时常量、require_query_password 存在）。
4. D 项：unsupported 贯穿 generate→execute→graph→response→API 状态码。
5. E 项：Top3 合计 25.27% 不画完整饼图；卖家金额/订单不同轴。
6. C 项：业务 code=500 的 HTTP 200 不能当作成功空匹配。

运行：
    D:\\python3.12.10\\python.exe agent\\test_rollback_regression.py
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from agent.redaction import sanitize_text, sanitize_data
from agent.nodes.response import _decide_response_type
from agent.nodes.sql_generation import sql_generation_node, _unsupported_reason
from agent.nodes.sql_execution import sql_execution_node


class ShortSecretRedactionTests(unittest.TestCase):
    def test_short_configured_secret_is_redacted(self):
        """4 字符合成密码也必须被替换（G 项新发现缺陷的回归）"""
        with patch.dict(os.environ, {"DATAMIND_QUERY_PASSWORD": "abcd", "DEEPSEEK_API_KEY": "xy"}):
            self.assertEqual(sanitize_text("password=abcd end"), "password=*** end")
            self.assertEqual(sanitize_text("key=xy end"), "key=*** end")

    def test_recursive_redaction_all_fields(self):
        with patch.dict(os.environ, {"DATAMIND_QUERY_PASSWORD": "abcd", "DEEPSEEK_API_KEY": "tok3n"}):
            payload = {
                "answer": "err tok3n",
                "sql": "SELECT 'abcd' AS x",
                "rows": [{"note": "tok3n", "nested": ["abcd", {"k": "tok3n"}]}],
                "chart_config": {"label": "abcd"},
            }
            cleaned = sanitize_data(payload)
            raw = str(cleaned)
            self.assertNotIn("abcd", raw)
            self.assertNotIn("tok3n", raw)
            self.assertEqual(cleaned["rows"][0]["nested"][0], "***")


class InterfaceConsistencyTests(unittest.TestCase):
    def test_config_exposes_llm_constants_and_password_guard(self):
        import agent.config as c
        self.assertGreater(c.LLM_CONNECT_TIMEOUT, 0)
        self.assertGreater(c.LLM_READ_TIMEOUT, 0)
        self.assertGreaterEqual(c.LLM_MAX_RETRIES, 0)
        self.assertTrue(hasattr(c, "require_query_password"))

    def test_fresh_llm_import(self):
        import agent.llm  # noqa: F401 - 导入即验证无 ImportError


class UnsupportedContractTests(unittest.TestCase):
    def test_period_distinct_customer_is_unsupported(self):
        reason = _unsupported_reason(
            "2017年去重下单用户数是多少？",
            "表名: dws_daily_active_users (数据库: datamind_dws)",
        )
        self.assertIsNotNone(reason)
        with patch("agent.nodes.sql_generation.chat", side_effect=AssertionError("不应调用模型")):
            state = sql_generation_node({
                "user_question": "2017年去重下单用户数是多少？", "chat_history": [],
                "schema_context": "表名: dws_daily_active_users (数据库: datamind_dws)", "retry_count": 0,
            })
        self.assertEqual(state["sql_error_type"], "unsupported")
        self.assertIsNone(state["sql"])
        with patch("agent.nodes.sql_execution.pymysql.connect") as connect:
            result = sql_execution_node(state)
        self.assertEqual(result["sql_error_type"], "unsupported")
        connect.assert_not_called()

    def test_configuration_and_unsupported_do_not_retry(self):
        import agent.graph as g
        for etype in ("configuration", "unsupported"):
            self.assertEqual(
                g.route_after_execution({"sql_error": "x", "sql_error_type": etype, "retry_count": 0}),
                "response",
            )
        self.assertEqual(
            g.route_after_execution({"sql_error": "x", "sql_error_type": "sql", "retry_count": 0}),
            "sql_generation",
        )


class ChartAndSchemaTests(unittest.TestCase):
    def test_top3_partial_ratio_not_full_pie(self):
        rows = [
            {"rank": 1, "category_english": "health_beauty", "total_gmv": 1437665.78, "gmv_ratio": 9.14},
            {"rank": 2, "category_english": "watches_gifts", "total_gmv": 1298292.47, "gmv_ratio": 8.25},
            {"rank": 3, "category_english": "bed_bath_table", "total_gmv": 1240386.13, "gmv_ratio": 7.88},
        ]
        rtype, cfg = _decide_response_type("品类前三名", rows)
        self.assertEqual(cfg["type"], "bar")
        self.assertEqual(cfg["y"], ["total_gmv"])
        self.assertEqual(cfg["unit"], "BRL")

    def test_http200_business_500_not_success(self):
        from agent.nodes.schema_retrieval import SchemaRetrievalError, schema_retrieval_node
        class Resp:
            status_code = 200
            def json(self):
                return {"code": 500, "message": "synthetic", "data": []}
        class Client:
            def __init__(self, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): return None
            def get(self, *a, **k): return Resp()
        with patch("agent.nodes.schema_retrieval._extract_keywords", return_value=["x"]), \
             patch("agent.nodes.schema_retrieval.httpx.Client", Client):
            with self.assertRaises(SchemaRetrievalError):
                schema_retrieval_node({"user_question": "测试", "chat_history": []})


if __name__ == "__main__":
    unittest.main(verbosity=2)
