# -*- coding: utf-8 -*-
"""集中修复的业务边界回归：不调用模型，不修改业务数据库。"""
from __future__ import annotations
import json
import os
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent.nodes import response as r, sql_generation as g, schema_retrieval as s, sql_execution as x
from agent.redaction import sanitize_data
from agent.result_semantics import output_specs


def answer(q, rows, sql=None, meta=None, truncated=False):
    with patch.object(r, "chat", side_effect=AssertionError("数据回答不得采用模型编造的数字")):
        return r.response_node({"user_question": q, "sql_result": rows, "sql": sql,
            "sql_meta": meta, "result_truncated": truncated, "sql_error": None, "sql_error_type": None})


class MetricFactsTests(unittest.TestCase):
    def test_ratio_never_currency_or_unrequested_sum(self):
        result = answer("品类GMV占比前三名，只返回占比", [
            {"category_english": "a", "gmv_ratio": 9.14}, {"category_english": "b", "gmv_ratio": 8.25},
            {"category_english": "c", "gmv_ratio": 7.88}])
        self.assertIn("9.14%", result["final_answer"])
        self.assertNotIn("R$", result["final_answer"])
        self.assertNotIn("25.27", result["final_answer"])
        self.assertEqual(result["chart_config"]["unit"], "percent")

    def test_averages_not_summed_or_truncated(self):
        out = answer("卖家客单价排行合计", [{"seller_id": "a", "avg_order_value": 12.75}, {"seller_id": "b", "avg_order_value": 20.5}])["final_answer"]
        self.assertIn("R$ 12.75", out)
        self.assertIn("R$ 20.50", out)
        self.assertNotIn("33.25", out)
        self.assertNotIn(" 单", out)

    def test_column_order_does_not_change_metric(self):
        for row in ({"order_date": "2018-08-01", "order_count": 5}, {"order_count": 5, "order_date": "2018-08-01"}):
            out = answer("每日订单趋势", [row])
            self.assertEqual(out["chart_config"]["y"], ["order_count"])
            self.assertIn("5 单", out["final_answer"])

    def test_money_sum_exact_and_only_on_request(self):
        rows = [{"seller_id": "a", "total_gmv": Decimal("0.10"), "total_orders": 2}, {"seller_id": "b", "total_gmv": Decimal("0.20"), "total_orders": 3}]
        out = answer("卖家销售额排行及合计", rows)["final_answer"]
        self.assertIn("合计：R$ 0.30", out)
        self.assertIn("非全局去重订单数", out)
        self.assertNotIn("合计", answer("卖家销售额排行", rows)["final_answer"])

    def test_month_counts_not_called_seller_counts(self):
        out = answer("各月订单排行合计", [{"year_month": "2017-01", "monthly_orders": 2}, {"year_month": "2017-02", "monthly_orders": 3}])["final_answer"]
        self.assertIn("订单数合计：5 单", out)
        self.assertNotIn("卖家", out)
        self.assertNotIn("非全局去重", out)

    def test_category_counts_have_correct_grain(self):
        out = answer("品类订单排行合计", [{"category_english": "a", "total_orders": 2}, {"category_english": "b", "total_orders": 3}])["final_answer"]
        self.assertIn("关联订单计数之和", out)
        self.assertNotIn("卖家", out)

    def test_null_is_not_zero(self):
        out = answer("总GMV", [{"total_gmv": None}])["final_answer"]
        self.assertIn("无数据", out)
        self.assertNotIn("0.00", out)

    def test_bad_and_nonfinite_numeric_are_controlled(self):
        for value in ("R$ 1,234.56", float("nan"), float("inf"), "1e9999", True):
            with self.subTest(value=str(value)):
                out = answer("销售额排行合计", [{"seller_id": "a", "total_gmv": value}])
                self.assertIn("不可用", out["final_answer"])
                self.assertIsNone(out["chart_config"])

    def test_partial_null_does_not_create_total(self):
        out = answer("GMV合计", [{"total_gmv": 10}, {"total_gmv": None}])["final_answer"]
        self.assertNotIn("合计", out)

    def test_sql_alias_retains_currency_and_dimension(self):
        sql = "SELECT category_english AS 品类, ROUND(total_gmv,2) AS 销售额 FROM datamind_ads.ads_category_rank"
        out = answer("GMV排行", [{"品类": "a", "销售额": 123.45}], sql)
        self.assertEqual(out["chart_config"]["unit"], "BRL")
        self.assertEqual(out["chart_config"]["x"], "品类")
        self.assertIn("R$ 123.45", out["final_answer"])

    def test_misleading_alias_does_not_override_expression(self):
        out = answer("GMV占比排行", [{"category_english": "a", "total_gmv": 9.14}], "SELECT category_english, gmv_ratio AS total_gmv FROM datamind_ads.ads_category_rank")
        self.assertEqual(out["chart_config"]["unit"], "percent")
        self.assertNotIn("R$", out["final_answer"])

    def test_cte_lineage_and_complex_expression_fallback(self):
        specs = output_specs("WITH x AS (SELECT total_gmv AS amount FROM datamind_ads.ads_category_rank) SELECT ROUND(amount,2) AS 销售额 FROM x", ["销售额"])
        self.assertEqual(specs["销售额"]["unit"], "BRL")
        spec = output_specs("SELECT total_gmv/100 AS total_orders FROM datamind_ads.ads_category_rank", ["total_orders"])
        self.assertEqual(spec["total_orders"]["kind"], "unknown")


class ScopeAndPeriodTests(unittest.TestCase):
    def test_historical_last_month_not_global_partial_month(self):
        out = answer("2017年最后一个月的订单数", [{"year_month": "2017-12", "monthly_orders": 5620}])["final_answer"]
        self.assertIn("2017-12", out)
        self.assertIn("5,620 单", out)
        self.assertNotIn("2018-09", out)
        self.assertNotIn("不足全月", out)
        self.assertNotIn("数据集中最后", out)

    def test_actual_partial_month_is_explained(self):
        out = answer("数据集最后一个月的订单数", [{"year_month": "2018-09", "monthly_orders": 1}])["final_answer"]
        self.assertIn("2018-09-03", out)
        self.assertIn("不足全月", out)
        self.assertIn("1 单", out)

    def test_leap_month_end_not_partial(self):
        out = answer("最新一个月订单数", [{"year_month": "2024-02", "monthly_orders": 3, "data_period_end": "2024-02-29"}])["final_answer"]
        self.assertNotIn("不足全月", out)
        self.assertNotIn("2018-09", out)

    def test_live_metadata_overrides_static_snapshot(self):
        out = answer("最新一个月订单数", [{"year_month": "2024-02", "monthly_orders": 3}],
            "SELECT year_month, monthly_orders FROM datamind_ads.ads_gmv_trend",
            {"data_period": {"data_period_end": "2024-02-29"}})["final_answer"]
        self.assertNotIn("不足全月", out)
        self.assertNotIn("2018-09", out)

    def test_no_metadata_means_no_claim_about_completeness(self):
        out = answer("最新一个月订单数", [{"year_month": "2018-09", "monthly_orders": 1}],
            "SELECT year_month, monthly_orders FROM datamind_ads.ads_gmv_trend")["final_answer"]
        self.assertNotIn("不足全月", out)

    def test_fake_cutoff_alias_does_not_replace_metadata(self):
        out = answer("最新一个月订单数", [{"year_month": "2018-09", "monthly_orders": 1, "data_period_end": "2018-09-30"}],
            "SELECT year_month, monthly_orders, '2018-09-30' AS data_period_end FROM datamind_ads.ads_gmv_trend",
            {"data_period": {"data_period_end": "2018-09-03"}})["final_answer"]
        self.assertIn("仅覆盖至 2018-09-03", out)

    def test_trend_uses_all_rows_for_peak_and_endpoint(self):
        rows = [{"year_month": f"{2000+i//12}-{i%12+1:02d}", "monthly_gmv": 9999 if i == 103 else i} for i in range(200)]
        out = answer("按月GMV趋势", rows)["final_answer"]
        self.assertIn(rows[0]["year_month"], out)
        self.assertIn(rows[-1]["year_month"], out)
        self.assertIn("9,999.00", out)
        self.assertNotIn("经营", out)

    def test_truncated_scope_no_full_total(self):
        out = answer("全部卖家销售额合计", [{"seller_id": "a", "total_gmv": 10}, {"seller_id": "b", "total_gmv": 20}], truncated=True)["final_answer"]
        self.assertIn("本次返回行GMV合计", out)
        self.assertIn("仅展示前 2 行", out)
        self.assertNotIn("全部卖家已列出", out)


class ChartBoundaryTests(unittest.TestCase):
    def test_order_question_uses_orders_not_average(self):
        out = answer("每月订单数趋势", [{"year_month": "2017-01", "avg_order_value": 10.5, "monthly_orders": 100}])
        self.assertEqual(out["chart_config"]["y"], ["monthly_orders"])
        self.assertEqual(out["chart_config"]["unit"], "orders")

    def test_multicategory_daily_trend_falls_back_to_table(self):
        out = answer("各品类每天GMV趋势", [{"category_english": "a", "order_date": "2018-08-01", "gmv": 1}, {"category_english": "b", "order_date": "2018-08-01", "gmv": 2}])
        self.assertEqual(out["response_type"], "table")
        self.assertIsNone(out["chart_config"])

    def test_no_dimension_never_money_against_itself(self):
        self.assertIsNone(answer("按月GMV趋势", [{"monthly_gmv": 100}, {"monthly_gmv": 200}])["chart_config"])

    def test_subset_percent_does_not_claim_global_denominator(self):
        out = answer("品类内部占比分布", [{"category_english": "a", "subset_ratio": 60}, {"category_english": "b", "subset_ratio": 40}])
        self.assertIsNone(out["chart_config"])
        _, cfg = r._decide_response_type("品类占比分布", [{"category_english": "a", "gmv_ratio": 60}, {"category_english": "b", "gmv_ratio": 40}])
        self.assertEqual(cfg["type"], "bar")
        self.assertNotIn("denominator", cfg)

    def test_full_ratio_requires_actual_category_count_and_no_truncation(self):
        sql = "SELECT category_english, gmv_ratio FROM datamind_ads.ads_category_rank"
        rows = [{"category_english": "a", "gmv_ratio": 60}, {"category_english": "b", "gmv_ratio": 40}]
        specs = output_specs(sql, list(rows[0]))
        self.assertEqual(r._decide_response_type("品类占比分布", rows, specs, category_count=2)[1]["type"], "pie")
        for count, truncated in ((3, False), (2, True), (None, False)):
            self.assertEqual(r._decide_response_type("品类占比分布", rows, specs, truncated, count)[1]["type"], "bar")

    def test_distinct_dates_do_not_join_different_categories_into_one_series(self):
        rows = [{"order_date": "2017-01-01", "category_english": "a", "gmv": 10},
                {"order_date": "2017-01-02", "category_english": "b", "gmv": 20}]
        self.assertIsNone(answer("每天各品类GMV趋势", rows)["chart_config"])


class ProtocolBoundaryTests(unittest.TestCase):
    def test_daily_queries_allowed_but_period_distinct_blocked(self):
        ctx = "dws_daily_active_users order_date active_user_count"
        for q in ("2018-08-01当天的去重下单用户数", "2017年每日去重下单用户数趋势", "2018年8月1日的独立客户数"):
            self.assertIsNone(g._unsupported_reason(q, ctx))
            self.assertEqual(s._extract_keywords(q)[0], "active_user_count")
        for q in ("2017年去重下单用户总数", "2018-08-01以来去重用户数", "2018-08-01到2018-08-02去重人数用户"):
            self.assertIsNotNone(g._unsupported_reason(q, ctx))

    def test_unsupported_normalized_before_sql_or_database(self):
        for raw in ("  UNSUPPORTED: 无此指标 ", "```sql\nUNSUPPORTED: 无此指标\n```", "```mysql\nUNSUPPORTED: 无此指标\n```", "```text\nUNSUPPORTED: 无此指标\n```"):
            with patch.object(g, "chat", return_value=raw), patch.object(x.pymysql, "connect") as connect:
                state = g.sql_generation_node({"user_question": "未知指标", "schema_context": "t", "chat_history": [], "retry_count": 0})
                result = x.sql_execution_node(state)
                self.assertEqual(result["sql_error_type"], "unsupported")
                connect.assert_not_called()

    def test_nested_schema_malformed_is_rejected(self):
        valid = {"table_name": "t", "database": "d", "table_desc": "x", "fields": [{"name": "x", "desc": "x"}]}
        self.assertTrue(s._valid_table(valid))
        for malformed in ({k: v for k, v in valid.items() if k != "table_desc"}, valid | {"fields": [{"name": "x"}]}, valid | {"fields": [None]}):
            self.assertFalse(s._valid_table(malformed))
        class Resp:
            status_code = 200
            def json(self): return {"code": 200, "data": [valid | {"fields": [None]}]}
        with patch.object(s, "_extract_keywords", return_value=["x"]), patch.object(s.httpx.Client, "get", return_value=Resp()):
            with self.assertRaises(s.SchemaRetrievalError):
                s.schema_retrieval_node({"user_question": "x"})

    def test_secret_keys_redacted_without_losing_colliding_values(self):
        with patch.dict(os.environ, {"DATAMIND_QUERY_PASSWORD": "synthetic-secret-key", "DEEPSEEK_API_KEY": "other-secret-key"}):
            out = sanitize_data({"synthetic-secret-key": 1, "other-secret-key": 2, "***#2": {"synthetic-secret-key": "other-secret-key"}})
            text = json.dumps(out)
            self.assertNotIn("synthetic-secret-key", text)
            self.assertNotIn("other-secret-key", text)
            self.assertEqual(len(out), 3)
            self.assertEqual(list(out.values())[:2], [1, 2])

    def test_period_metadata_read_and_missing_case(self):
        from unittest.mock import Mock
        cursor = Mock()
        cursor.fetchmany.return_value = [{"data_period_start": "2024-01-01", "data_period_end": "2024-02-29"}]
        self.assertEqual(x._read_data_period(cursor)["data_period_end"], "2024-02-29")
        cursor.fetchmany.return_value = [{"data_period_start": None}]
        self.assertEqual(x._read_data_period(cursor), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
