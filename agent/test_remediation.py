# -*- coding: utf-8 -*-
"""本轮返修的定向回归测试（不调用 LLM、不连接数据库）

覆盖独立验收报告中的四项问题：
1. 只读边界：换行分隔的 INTO/OUTFILE、反引号限定系统库、非授权表、加锁读、
   系统变量、危险函数、多语句
2. 行数上限：LIMIT count / LIMIT offset,count / LIMIT count OFFSET offset、
   子查询 LIMIT 不干扰、字符串里的 "limit 1" 不干扰、LIMIT 表达式被拒
3. SQL 文本处理：字符串内分号保留、字符串字面量不被表名补全改写
4. 趋势总结完整性：25 个月完整序列进入总结输入（含最新月份），金额口径为 BRL

运行方式：
    cd D:\\DataMind
    D:\\python3.12.10\\python.exe agent\\test_remediation.py
"""

import sys
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.nodes import response as response_module
from agent.nodes.sql_generation import _extract_sql
from agent.sql_guard import (
    MAX_ROWS,
    SqlGuardError,
    extract_table_db,
    prepare_readonly_query,
)


def expect_denied(sql: str, keyword: str, table_db=None) -> None:
    """断言 SQL 被拒绝，且错误信息包含关键词"""
    try:
        prepare_readonly_query(sql, table_db)
    except SqlGuardError as exc:
        assert keyword in exc.message, f"错误信息不含「{keyword}」：{exc.message}"
        return
    raise AssertionError(f"未被拒绝：{sql!r}")


def test_readonly_bypass_cases():
    """独立验收报告复现的绕过输入必须全部被拒"""
    # 1) INTO 与 OUTFILE 之间换行（原实现用关键词包含匹配时可绕过）
    expect_denied("SELECT 1 INTO\nOUTFILE 'D:/DataMind/do_not_execute.txt'", "拒绝")
    # 2) SELECT ... INTO 变量形式（能被解析成 Into 节点）
    expect_denied("SELECT 1 INTO @x", "INTO")
    # 3) 反引号限定的系统库
    expect_denied("SELECT User, Host FROM `mysql`.`user`", "禁止访问库 mysql")
    expect_denied("SELECT * FROM information_schema.tables", "禁止访问库")
    expect_denied("SELECT * FROM sys.session", "禁止访问库")
    # 4) Schema 外的库 / 非授权表
    expect_denied("SELECT * FROM datamind_ods.ods_orders", "禁止访问库 datamind_ods")
    expect_denied("SELECT * FROM datamind_ads.ads_not_exist", "不在授权范围内")
    # 5) 写操作与 DDL
    for bad in (
        "INSERT INTO datamind_ads.ads_gmv_overview VALUES (1)",
        "UPDATE datamind_ads.ads_gmv_overview SET total_gmv = 0",
        "DELETE FROM datamind_ads.ads_gmv_overview",
        "DROP TABLE datamind_ads.ads_gmv_overview",
        "CREATE TABLE t (a INT)",
        "SHOW TABLES",
    ):
        expect_denied(bad, "只允许只读查询")
    # 6) 加锁读、系统变量、危险函数
    expect_denied("SELECT * FROM datamind_ads.ads_gmv_overview FOR UPDATE", "加锁读")
    expect_denied("SELECT @@datadir", "系统变量")
    expect_denied("SELECT load_file('/etc/passwd')", "load_file")
    expect_denied("SELECT sleep(5)", "sleep")
    # 7) 真正的多语句
    expect_denied("SELECT 1; SELECT 2", "多语句")
    expect_denied("SELECT 1; DROP TABLE datamind_ads.ads_gmv_overview", "多语句")

    # 合法查询不受影响
    ok_cases = [
        "SELECT total_gmv FROM datamind_ads.ads_gmv_overview",
        "SELECT `year_month`, monthly_gmv FROM datamind_ads.ads_gmv_trend ORDER BY `year_month`",
        "SELECT a.category_english FROM ads_category_rank a "
        "JOIN dws_category_summary b ON a.category_english = b.category_english",
        "WITH t AS (SELECT 1 AS x) SELECT * FROM t",
    ]
    for sql in ok_cases:
        out, meta = prepare_readonly_query(sql)
        assert out, f"合法查询被误拒：{sql}"
        # CTE-only 查询没有真实表引用，其余应记录到白名单表
        if "ads_" in sql or "dws_" in sql:
            assert meta["tables"], f"未记录引用表：{meta}"
    print("  1. 只读边界绕过用例 全部按预期拒绝 通过")


def test_limit_forms():
    """最外层行数上限覆盖各种 LIMIT 写法"""
    out, meta = prepare_readonly_query("SELECT 1 LIMIT 0, 999999")
    assert f"LIMIT {MAX_ROWS}" in out and "OFFSET 0" in out, out
    assert meta["limit_action"] == "capped"

    out, meta = prepare_readonly_query("SELECT 1 LIMIT 999999 OFFSET 20")
    assert "LIMIT 1000" in out and "OFFSET 20" in out, out  # offset 语义保留
    assert meta["limit_action"] == "capped"

    out, meta = prepare_readonly_query("SELECT 1 LIMIT 50 OFFSET 500")
    assert "LIMIT 50" in out and meta["limit_action"] == "kept", (out, meta)

    # 子查询的 LIMIT 不应被当成外层 LIMIT
    out, meta = prepare_readonly_query("SELECT * FROM (SELECT 1 LIMIT 1) t")
    assert meta["limit_action"] == "added", meta
    assert out.upper().count("LIMIT") == 2, out  # 内层 1 + 外层 1000
    assert "LIMIT 1" in out and "LIMIT 1000" in out, out

    # 字符串里的 limit 1 不干扰判断
    out, meta = prepare_readonly_query("SELECT 'limit 1' AS s")
    assert meta["limit_action"] == "added", meta
    assert out.endswith("LIMIT 1000"), out

    # 非字面量 LIMIT 拒绝（避免绕过上限）
    expect_denied("SELECT 1 LIMIT 1+1", "整数字面量")
    expect_denied("SELECT 1 LIMIT @@max_allowed_packet", "系统变量")
    print("  2. LIMIT 各种写法与子查询/字符串干扰 通过")


def test_sql_text_handling():
    """字符串内分号保留、真正的多语句拒绝、表名补全不改字面量"""
    # 字符串里的分号必须保留（旧实现按 ; 切分会截断）
    sql = _extract_sql("```sql\nSELECT 'a;b' AS note;\n```")
    assert "'a;b'" in sql, sql
    out, _ = prepare_readonly_query(sql)
    assert "'a;b'" in out, out

    # 真正的多语句明确拒绝
    multi = _extract_sql("SELECT 1; SELECT 2")
    expect_denied(multi, "多语句")

    # 表名补全只改真实表引用，不改字符串字面量
    out, meta = prepare_readonly_query(
        "SELECT 'ads_gmv_overview' AS label FROM ads_gmv_overview"
    )
    assert "'ads_gmv_overview' AS `label`" in out or "'ads_gmv_overview' AS label" in out, out
    assert "`datamind_ads`.`ads_gmv_overview`" in out, out
    assert meta["tables"] == ["datamind_ads.ads_gmv_overview"], meta

    # Schema 上下文里的表名→库名映射可解析
    mapping = extract_table_db("表名: ads_gmv_overview (数据库: datamind_ads)")
    assert mapping == {"ads_gmv_overview": "datamind_ads"}
    print("  3. SQL 文本处理（分号/字面量/多语句） 通过")


def test_trend_summary_full_series():
    """趋势回答由实际序列确定性生成，保留首尾、峰值与不完整末月。"""
    rows = [
        {"year_month": f"2017-{m:02d}", "monthly_gmv": 1000.0 + m, "monthly_orders": m}
        for m in range(1, 13)
    ] + [
        {"year_month": f"2018-{m:02d}", "monthly_gmv": 2000.0 + m, "monthly_orders": m}
        for m in range(1, 13)
    ] + [{"year_month": "2018-09", "monthly_gmv": 3063.0, "monthly_orders": 1}]

    from agent.nodes.response import _compute_verified_facts, _summarize
    with patch.object(response_module, "chat", side_effect=AssertionError("回答不依赖模型生成数字")):
        answer = _summarize("GMV 按月的趋势怎么样？", rows,
                            facts=_compute_verified_facts("GMV 按月的趋势怎么样？", rows))
    assert "2017-01" in answer and "2018-09" in answer
    assert "2018-12" in answer  # 序列峰值
    assert "R$" in answer and "不足全月" in answer
    assert "经营下滑" not in answer
    print("  4. 趋势首尾、峰值与月覆盖通过")


def test_large_result_sampling_scope():
    """大结果集摘要和前端提示不得将局部展示说成全量。"""
    rows = [{"id": i, "gmv": float(1000 - i)} for i in range(200)]
    assert "本次查询返回 200 行" in response_module._summarize("列出全部数据", rows)
    assert "部分记录" in response_module._scope_suffix(200, truncated=False)
    assert "仅展示前" in response_module._scope_suffix(200, truncated=True)
    print("  5. 大结果集抽样范围说明 通过")


def test_retry_routing():
    """图级故障注入：SQL 错误重试受控、连接失败不重试、错误类别传递到状态"""
    import agent.graph as graph_module

    calls = {"exec": 0, "gen": 0}

    def fake_gen(state):
        calls["gen"] += 1
        return {**state, "sql": "SELECT 1", "sql_error": None}

    def fake_intent(state):
        return {**state, "intent": "data_query"}

    def fake_schema(state):
        return {**state, "schema_context": "表名: ads_gmv_overview (数据库: datamind_ads)"}

    def make_exec(fail_times: int, error_type: str, message: str):
        def fake_exec(state):
            calls["exec"] += 1
            if calls["exec"] <= fail_times:
                return {
                    **state,
                    "sql_error": message,
                    "sql_error_type": error_type,
                    "sql_result": None,
                }
            return {
                **state,
                "sql_error": None,
                "sql_error_type": None,
                "sql_result": [{"total_gmv": 1.0}],
            }

        return fake_exec

    # 替换图内的节点引用后重建图（不影响已编译的单例）
    originals = (
        graph_module.intent_node,
        graph_module.schema_retrieval_node,
        graph_module.sql_generation_node,
        graph_module.sql_execution_node,
    )
    graph_module.intent_node = fake_intent
    graph_module.schema_retrieval_node = fake_schema
    graph_module.sql_generation_node = fake_gen
    try:
        # 场景 1：SQL 连续失败 → 最多重试 2 次，返回 sql 类错误
        calls.update(exec=0, gen=0)
        graph_module.sql_execution_node = make_exec(99, "sql", "Unknown column")
        g = graph_module.build_graph()
        out = g.invoke({
            "user_question": "总GMV", "chat_history": [], "retry_count": 0,
            "result_truncated": False, "sql_meta": None,
        })
        assert calls["exec"] == 3, f"应执行 3 次（1 次 + 重试 2 次），实际 {calls['exec']}"
        assert out["retry_count"] == 2, out["retry_count"]
        assert out["error_category"] == "sql", out

        # 场景 2：一次失败后成功 → 只重试 1 次并最终成功
        calls.update(exec=0, gen=0)
        graph_module.sql_execution_node = make_exec(1, "sql", "Unknown column")
        g = graph_module.build_graph()
        out = g.invoke({
            "user_question": "总GMV", "chat_history": [], "retry_count": 0,
            "result_truncated": False, "sql_meta": None,
        })
        assert calls["exec"] == 2, calls
        assert out["error_category"] is None, out
        assert out["sql_result"], out

        # 场景 3：连接失败 → 不重试，返回 connection
        calls.update(exec=0, gen=0)
        graph_module.sql_execution_node = make_exec(99, "connection", "Can't connect")
        g = graph_module.build_graph()
        out = g.invoke({
            "user_question": "总GMV", "chat_history": [], "retry_count": 0,
            "result_truncated": False, "sql_meta": None,
        })
        assert calls["exec"] == 1, f"连接失败不应重试，实际执行 {calls['exec']} 次"
        assert out["error_category"] == "connection", out

        # 场景 4：空结果 → 成功，error_category 为空
        graph_module.sql_execution_node = lambda s: {
            **s, "sql_result": [], "sql_error": None, "sql_error_type": None,
        }
        g = graph_module.build_graph()
        out = g.invoke({
            "user_question": "总GMV", "chat_history": [], "retry_count": 0,
            "result_truncated": False, "sql_meta": None,
        })
        assert out["error_category"] is None, out
        assert "没有匹配到任何数据" in out["final_answer"], out["final_answer"]
    finally:
        (
            graph_module.intent_node,
            graph_module.schema_retrieval_node,
            graph_module.sql_generation_node,
            graph_module.sql_execution_node,
        ) = originals
    print("  6. 图级重试与错误类别传递 通过")


if __name__ == "__main__":
    print("返修定向回归测试：")
    test_readonly_bypass_cases()
    test_limit_forms()
    test_sql_text_handling()
    test_trend_summary_full_series()
    test_large_result_sampling_scope()
    test_retry_routing()
    print()
    print("返修定向测试全部通过")
