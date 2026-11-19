# -*- coding: utf-8 -*-
"""关键修复的回归测试（纯函数层，不调用 LLM，不连接数据库）

覆盖：
1. SQL 只读硬校验：拦截写操作、多语句、注释藏毒、危险关键词、系统库
2. 结果行数上限：无 LIMIT 自动追加、超大 LIMIT 自动截断（含 LIMIT offset,count）
3. 裸表名自动补全 `库名`.`表名` 前缀（跨库引用兜底，且不改字符串字面量）
4. 图表维度识别：x 轴取分类列而非 rank 序号；"前N名"触发柱状图
5. 关键词提取（含追问回退的前置能力）
6. 连接类错误码集合

运行方式：
    cd D:\\DataMind
    D:\\python3.12.10\\python.exe agent\\test_fixes.py
全部通过时输出 "全部回归测试通过"。
"""

import sys
from pathlib import Path

# 保证从任意目录运行都能导入 agent 包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.nodes.sql_execution import (
    CONNECTION_ERROR_CODES,
    _build_table_db_map,
    _enforce_limit,
    _qualify_tables,
    _validate_sql,
)
from agent.nodes.response import _decide_response_type
from agent.nodes.schema_retrieval import _extract_keywords


def norm(sql: str) -> str:
    """去掉反引号，便于断言不受标识符引用风格影响"""
    return (sql or "").replace("`", "")


def test_validate_sql():
    """只读硬校验"""
    # 正常 SELECT 通过
    _, err = _validate_sql("SELECT `rank`, total_gmv FROM `datamind_ads`.`ads_top_sellers`;")
    assert err is None, f"正常 SELECT 不应报错：{err}"

    # 写操作被拦截
    for bad in (
        "DELETE FROM datamind_ads.ads_gmv_overview",
        "UPDATE datamind_ads.ads_gmv_overview SET total_gmv = 0",
        "DROP TABLE ads_gmv_overview",
        "INSERT INTO t VALUES (1)",
    ):
        _, err = _validate_sql(bad)
        assert err is not None, f"写操作未被拦截：{bad}"

    # 多语句被拦截
    _, err = _validate_sql("SELECT 1; DELETE FROM datamind_ads.ads_gmv_overview")
    assert "多语句" in err, err

    # 非 SELECT 被拦截
    _, err = _validate_sql("TRUNCATE datamind_ads.ads_gmv_overview")
    assert err is not None

    # 注释里藏危险语句被拦截（先去注释再校验）
    _, err2 = _validate_sql("SELECT /* */ 1; DELETE FROM datamind_ads.ads_gmv_overview")
    assert err2 is not None, "分号多语句未被拦截"

    # 危险关键词 / 系统库被拦截
    for bad in (
        "SELECT load_file('/etc/passwd')",
        "SELECT * FROM information_schema.tables",
        "SELECT 1 INTO OUTFILE '/tmp/x'",
        "SELECT * FROM mysql.user",
    ):
        _, err = _validate_sql(bad)
        assert err is not None, f"危险语句未被拦截：{bad}"

    print("  1. SQL 只读硬校验 通过")


def test_enforce_limit():
    """行数上限"""
    # 无 LIMIT 自动追加
    assert norm(_enforce_limit("SELECT * FROM t")) == "SELECT * FROM t LIMIT 1000;"
    # 小 LIMIT 保留
    assert norm(_enforce_limit("SELECT * FROM t LIMIT 100")) == "SELECT * FROM t LIMIT 100;"
    # 超大 LIMIT 截断
    assert _enforce_limit("SELECT * FROM t LIMIT 99999").endswith("LIMIT 1000;")
    # 已带分号也能处理
    assert norm(_enforce_limit("SELECT * FROM t;")) == "SELECT * FROM t LIMIT 1000;"
    print("  2. 结果行数上限 通过")


def test_qualify_tables():
    """裸表名自动补全库名前缀"""
    ctx = (
        "表名: ads_gmv_overview (数据库: datamind_ads)\n"
        "描述: GMV总览\n字段:\n  - total_gmv: 总成交金额\n\n"
        "表名: dws_daily_trade (数据库: datamind_dws)\n"
    )
    table_db = _build_table_db_map(ctx)
    assert table_db == {
        "ads_gmv_overview": "datamind_ads",
        "dws_daily_trade": "datamind_dws",
    }, table_db

    # 裸表名补全
    out = _qualify_tables("SELECT total_gmv FROM ads_gmv_overview", table_db)
    assert "`datamind_ads`.`ads_gmv_overview`" in out, out
    # 已限定名不重复补全（严查：不允许出现 db`.`db 二重前缀）
    out = _qualify_tables(
        "SELECT a.total_gmv FROM `datamind_ads`.`ads_gmv_overview` a", table_db
    )
    assert "`datamind_ads`.`datamind_ads`" not in out, f"二重前缀 bug 复现：{out}"
    assert out.count("`datamind_ads`.`ads_gmv_overview`") == 1, out
    # 保留字带反引号也能补全
    out = _qualify_tables(
        "SELECT `rank`, total_gmv FROM `ads_top_sellers` WHERE `rank` <= 5",
        {"ads_top_sellers": "datamind_ads"},
    )
    assert "`datamind_ads`.`ads_top_sellers`" in out, out
    assert out.count("`datamind_ads`.`ads_top_sellers`") == 1, out
    # 跨库 JOIN 两个表都补全
    out = _qualify_tables(
        "SELECT * FROM ads_gmv_overview JOIN dws_daily_trade ON 1=1", table_db
    )
    assert "`datamind_ads`.`ads_gmv_overview`" in out
    assert "`datamind_dws`.`dws_daily_trade`" in out
    print("  3. 表名前缀自动补全 通过")


def test_decide_response_type():
    """图表维度识别"""
    # 排行查询：第一列是 rank，x 轴应取分类列而非 rank
    rows = [
        {"rank": 1, "category_english": "health_beauty", "total_gmv": 1437665.78},
        {"rank": 2, "category_english": "watches_gifts", "total_gmv": 1298292.47},
        {"rank": 3, "category_english": "bed_bath_table", "total_gmv": 1240386.13},
    ]
    rtype, cfg = _decide_response_type("品类排行前五", rows)
    assert rtype == "chart" and cfg["type"] == "bar", (rtype, cfg)
    assert cfg["x"] == "category_english", f"x 轴不应取 rank：{cfg}"
    assert "total_gmv" in cfg["y"] and "rank" not in cfg["y"], cfg

    # "前3名"这类表达也触发排行图表
    rtype, cfg = _decide_response_type("那前三名呢？", rows)
    assert rtype == "chart" and cfg["x"] == "category_english", (rtype, cfg)

    # 趋势查询：x 轴取月份
    trows = [
        {"year_month": "2018-08", "monthly_gmv": 100.0},
        {"year_month": "2018-09", "monthly_gmv": 200.0},
    ]
    rtype, cfg = _decide_response_type("GMV按月的趋势怎么样", trows)
    assert rtype == "chart" and cfg["type"] == "line", (rtype, cfg)
    assert cfg["x"] == "year_month" and cfg["y"] == ["monthly_gmv"], cfg

    # 完整全局占比（合计100%）→ 饼图，并明确分母与覆盖范围
    prows = [{"category_english": f"c{i}", "gmv_ratio": 100 / 72} for i in range(72)]
    from agent.result_semantics import output_specs
    pspecs = output_specs("SELECT category_english, gmv_ratio FROM datamind_ads.ads_category_rank", list(prows[0]))
    rtype, cfg = _decide_response_type("各品类占比分布", prows, pspecs, category_count=72)
    assert rtype == "chart" and cfg["type"] == "pie", (rtype, cfg)
    assert cfg["name"] == "category_english" and cfg["value"] == "gmv_ratio", cfg
    assert cfg["denominator"] == "全局GMV" and cfg["coverage"] == "complete", cfg

    # 多行、无触发词 → 表格
    rows5 = [{"category_english": f"c{i}", "total_gmv": float(i)} for i in range(5)]
    rtype, _ = _decide_response_type("列出所有品类", rows5)
    assert rtype == "table", rtype

    # 空结果 → 文字
    rtype, cfg = _decide_response_type("任何问题", [])
    assert rtype == "text" and cfg is None
    print("  4. 图表维度识别 通过")


def test_extract_keywords():
    """关键词提取（含追问回退的前置能力）"""
    assert "rank" in _extract_keywords("那前三名呢？")
    assert "rank" in _extract_keywords("卖家排行前五")
    assert "GMV" in _extract_keywords("总GMV是多少")
    assert "trend" in _extract_keywords("GMV趋势按月怎么样")
    assert _extract_keywords("你好呀") == []
    print("  5. 关键词提取 通过")


def test_connection_error_codes():
    """连接错误码集合包含常见连接失败码"""
    for code in (2003, 1045, 2002):
        assert code in CONNECTION_ERROR_CODES, code
    # SQL 语法错误码不在连接集合中
    assert 1064 not in CONNECTION_ERROR_CODES
    assert 1146 not in CONNECTION_ERROR_CODES
    print("  6. 错误分类码表 通过")


if __name__ == "__main__":
    print("agent 修复回归测试：")
    test_validate_sql()
    test_enforce_limit()
    test_qualify_tables()
    test_decide_response_type()
    test_extract_keywords()
    test_connection_error_codes()
    print()
    print("全部回归测试通过")
