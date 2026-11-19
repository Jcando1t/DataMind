# -*- coding: utf-8 -*-
"""最后一轮定向修复：真实模型联调复测（保存脱敏证据 JSON）。

覆盖：卖家排行、最新月份订单、月度 GMV 趋势、品类追问、GMV→月订单追问。
对每项核对 HTTP 状态、SQL、结果行、回答文字、图表配置，并做确定性断言。
不输出真实密码/密钥/完整连接串。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
EVID = ROOT / "docs/evidence_final_2026-11-17/live_recheck.json"
BASE = "http://localhost:8000/api/chat"

CASES = [
    {"key": "sellers", "question": "卖家销售额排行前20"},
    {"key": "latest", "question": "数据集中最后一个月的订单数是多少？"},
    {"key": "trend", "question": "GMV按月的趋势怎么样？"},
    {"key": "category_follow", "question": "哪个品类销售额最高？", "follow": "那前三名呢？"},
    {"key": "orders_follow", "question": "GMV按月的趋势怎么样？", "follow": "那订单数按月呢？"},
]


def _post(session, question, history=None):
    payload = {"question": question}
    if history:
        payload["chat_history"] = history
    r = session.post(BASE, json=payload, timeout=120)
    body = r.json()
    return {
        "http_status": r.status_code,
        "code": body.get("code"),
        "data": body.get("data") or {},
    }


def _check(key, ans, data):
    """返回 (passed, notes, assertions)。"""
    notes, asserts = [], []
    sql = data.get("sql") or ""
    rows = data.get("rows") or []
    chart = data.get("chart_config")
    ok = True

    def A(name, cond):
        nonlocal ok
        asserts.append({"name": name, "passed": bool(cond)})
        if not cond:
            ok = False

    if key == "sellers":
        A("http_200", data_http == 200)
        A("rows_20", len(rows) == 20)
        gmv_sum = round(sum(float(r.get("total_gmv") or 0) for r in rows), 2)
        ord_sum = sum(int(r.get("total_orders") or 0) for r in rows)
        A("gmv_sum_exact", gmv_sum == 3292633.06)
        A("orders_sum_exact", ord_sum == 18908)
        A("answer_has_exact_gmv", "3,292,633.06" in ans)
        A("answer_no_wrong_estimate", "3,344,000" not in ans and "20,000" not in ans)
        A("orders_caliber_stated", ("非全局去重" in ans) or ("跨卖家可能重复" in ans))
        A("chart_single_metric_bar", bool(chart) and chart.get("type") == "bar" and chart.get("y") == ["total_gmv"])
        A("chart_unit_brl", bool(chart) and chart.get("unit") == "BRL")
        notes.append(f"gmv_sum={gmv_sum} orders_sum={ord_sum}")
    elif key == "latest":
        A("http_200", data_http == 200)
        A("single_row", len(rows) == 1 and rows[0].get("year_month") == "2018-09" and rows[0].get("monthly_orders") == 1)
        A("answer_last_recorded_month", "最后有记录月份" in ans and "2018-09" in ans)
        A("answer_order_count", "1 单" in ans)
        A("answer_period_end", "2018-09-03" in ans)
        A("answer_not_complete_month", "完整月份" not in ans and "最后一个完整" not in ans)
        A("answer_no_garbled", "是不最后" not in ans and "不能当作最后有记录月份" not in ans)
    elif key == "trend":
        A("http_200", data_http == 200)
        A("rows_25", len(rows) == 25)
        A("last_month_2018_09", rows[-1].get("year_month") == "2018-09" and rows[-1].get("monthly_gmv") == 166.46)
        A("trend_partial_month_note", "覆盖至" in ans or "仅覆盖" in ans)
        A("trend_not_complete_claim", "最后一个完整月份" not in ans)
        A("chart_line_gmv_brl", bool(chart) and chart.get("type") == "line" and chart.get("y") == ["monthly_gmv"] and chart.get("unit") == "BRL")
    elif key == "category_follow":
        A("http_200", data_http == 200)
        A("rows_3", len(rows) == 3)
        A("top1_health_beauty", "health_beauty" in ans and "1,437,665.78" in ans)
        A("chart_bar", bool(chart) and chart.get("type") == "bar")
    elif key == "orders_follow":
        A("http_200", data_http == 200)
        A("rows_25", len(rows) == 25)
        A("orders_unit", bool(chart) and chart.get("y") == ["monthly_orders"] and chart.get("unit") == "orders")
        A("answer_last_month_scoped", "2018-09" in ans and ("覆盖至" in ans or "仅覆盖" in ans))
    return ok, notes, asserts


if __name__ == "__main__":
    results = []
    with httpx.Client() as s:
        # 服务健康
        h = s.get("http://localhost:8000/health", timeout=10)
        for case in CASES:
            item = {"key": case["key"], "question": case["question"], "history_used": bool(case.get("follow"))}
            first = _post(s, case["question"])
            data_http = first["http_status"]
            data = first["data"]
            ans = data.get("answer") or ""
            if case.get("follow"):
                history = [
                    {"role": "user", "content": case["question"]},
                    {"role": "assistant", "content": ans},
                ]
                second = _post(s, case["follow"], history)
                data_http = second["http_status"]
                data = second["data"]
                ans = data.get("answer") or ""
                item["follow_question"] = case["follow"]
            passed, notes, asserts = _check(case["key"], ans, data)
            item.update({
                "http_status": data_http,
                "code": data.get("code"),
                "answer": ans,
                "sql": data.get("sql"),
                "row_count": len(data.get("rows") or []),
                "rows": data.get("rows"),
                "chart_config": data.get("chart_config"),
                "notes": notes,
                "assertions": asserts,
                "passed": passed,
            })
            results.append(item)
            print(f"[{case['key']}] http={data_http} passed={passed} asserts={sum(1 for a in asserts if a['passed'])}/{len(asserts)}")
            for a in asserts:
                if not a["passed"]:
                    print("   FAIL:", a["name"])

    out = {
        "generated_at": datetime.now().isoformat(),
        "mode": "live model recheck after targeted fix",
        "endpoint": BASE,
        "results": results,
        "summary": {
            "total": len(results),
            "passed": sum(1 for r in results if r["passed"]),
            "failed": sum(1 for r in results if not r["passed"]),
        },
    }
    EVID.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("证据文件:", EVID, "summary=", out["summary"])
