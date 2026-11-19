# -*- coding: utf-8 -*-
"""节点5：根据查询结果生成事实回答，文本与图表共用指标语义。"""
from __future__ import annotations

import calendar
import re
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from agent.llm import chat
from agent.result_semantics import number, output_specs
from agent.state import AgentState
from backend.data_dictionary import DATA_PERIOD_START, DATA_PERIOD_END
from datamind_config import load_dataset_config

_DATASET_CONFIG = load_dataset_config()

FULL_SERIES_MAX_ROWS = 100


def _is_rank_question(question: str) -> bool:
    return bool(re.search(r"前\s*[0-9一二三四五六七八九十百]+\s*[名大个]", question or "")
                or any(w in (question or "").lower() for w in ("排行", "排名", "top")))


def _compute_verified_facts(question: str, rows: list, sql: str | None = None, meta: dict | None = None) -> dict:
    columns = list(rows[0]) if rows else []
    specs = output_specs(sql, columns)
    # 无 SQL 的单元输入使用当前静态数据快照；真实执行使用同连接读取的覆盖元数据。
    coverage = (meta or {}).get("data_period") or ({} if sql else {"data_period_start": DATA_PERIOD_START, "data_period_end": DATA_PERIOD_END})
    coverage = dict(coverage)
    for key in ("data_period_start", "data_period_end"):
        for row in rows:
            if row.get(key) and (not sql or specs.get(key, {}).get("source_column") == key
                                and specs.get(key, {}).get("source_table") == "ads_gmv_overview"):
                coverage[key] = str(row[key])[:10]
                break
    facts = {"question": question, "rows": rows, "specs": specs, "coverage": coverage,
             "is_rank": _is_rank_question(question),
             "is_latest": any(w in question for w in ("最后", "最新", "最近一期", "末月")),
             "is_trend": any(w in question for w in ("趋势", "每月", "月份", "按月", "走势", "月度", "每日", "逐日")),
             "period_end": coverage.get("data_period_end"), "row_count": len(rows)}
    totals = {}
    # 多源查询的连接可能重复度量，无法确认可加性时不自动计算合计。
    if len((meta or {}).get("tables", [])) <= 1:
        for col, spec in specs.items():
            if spec["kind"] not in {"money", "orders"}:
                continue
            values = [number(row.get(col)) for row in rows]
            if values and all(v is not None for v in values):
                if spec["kind"] == "orders" and any(v != v.to_integral_value() for v in values):
                    continue
                totals[col] = sum(values, Decimal(0))
    facts["totals"] = totals
    return facts


def _format_value(value, spec: dict) -> str:
    if value is None:
        return "无数据"
    if spec["kind"] in {"money", "average", "orders", "users", "daily_users", "count", "percent", "rank"}:
        n = number(value)
        if n is None:
            return "不可用"
        if spec["unit"] in {"orders", "users", "count"} and spec["kind"] != "average":
            if n != n.to_integral_value():
                return "不可用"
            return f"{n:,.0f}" + {"orders": " 单", "users": " 人", "count": ""}[spec["unit"]]
        text = f"{n.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f}"
        return {_DATASET_CONFIG["currency"]: f"{_DATASET_CONFIG['currency_symbol']} {text}", "percent": text + "%", "orders": text + " 单", "users": text + " 人"}.get(spec["unit"], text)
    return str(value)


def _coverage_note(facts: dict) -> str:
    months = {str(row[col])[:7] for row in facts["rows"] for col, s in facts["specs"].items()
              if s["kind"] == "month" and row.get(col)}
    notes = []
    for key, edge in (("data_period_start", "start"), ("data_period_end", "end")):
        try:
            d = date.fromisoformat(str(facts["coverage"][key])[:10])
        except (KeyError, ValueError, TypeError):
            continue
        partial = d.day != (1 if edge == "start" else calendar.monthrange(d.year, d.month)[1])
        if d.strftime("%Y-%m") in months and partial:
            notes.append(f"{d:%Y-%m}仅覆盖{'自' if edge == 'start' else '至'} {d}，不足全月")
    return "；".join(notes) + "，不能直接与完整月比较或据此判断经营变化。" if notes else ""


def _row_text(row: dict, specs: dict) -> str:
    return "，".join(f"{specs[col]['label']}：{_format_value(value, specs[col])}"
                    for col, value in row.items() if col in specs and specs[col]["kind"] != "rank")


def _choose_metric(question: str, value_cols: list[str], specs: dict | None = None) -> str | None:
    specs = specs or output_specs(None, value_cols)
    q = question.lower()
    wanted = None
    if any(w in q for w in ("环比", "增长率")):
        wanted = [c for c in value_cols if specs[c]["source_column"] == "mom_growth_rate"]
    elif any(w in q for w in ("占比", "比例", "分布")):
        wanted = [c for c in value_cols if specs[c]["kind"] == "percent"]
    elif any(w in q for w in ("客单价", "均价", "平均")):
        wanted = [c for c in value_cols if specs[c]["kind"] == "average"]
    elif any(w in q for w in ("用户", "客户", "user", "customer")):
        wanted = [c for c in value_cols if specs[c]["kind"] in {"users", "daily_users"}]
    elif any(w in q for w in ("订单", "单量")) and not any(w in q for w in ("gmv", "销售额", "金额")):
        wanted = [c for c in value_cols if specs[c]["kind"] == "orders"]
    if wanted is not None:
        return sorted(wanted)[0] if wanted else None
    money = sorted(c for c in value_cols if specs[c]["kind"] == "money")
    return money[0] if money else (sorted(value_cols)[0] if value_cols else None)


def _summarize(question: str, rows: list, truncated: bool = False, facts: dict | None = None) -> str:
    """只陈述返回行和已验证聚合，不采用模型生成的数字或经营归因。"""
    facts = facts or _compute_verified_facts(question, rows)
    specs = facts["specs"]
    if not rows:
        return "没有匹配数据。"
    time_cols = sorted(c for c, s in specs.items() if s["kind"] in {"month", "date"})
    preview = rows[:3]
    intro = f"本次查询返回 {len(rows)} 行。" if len(rows) > 1 else ""
    if facts["is_latest"] and len(rows) == 1 and any(s["kind"] == "month" for s in specs.values()):
        intro = "所查询范围最后有记录月份及指标："
    if facts["is_trend"] and time_cols and len(rows) > 1:
        time_col = time_cols[0]
        ordered = sorted((r for r in rows if r.get(time_col) is not None), key=lambda r: str(r[time_col]))
        if ordered:
            preview = [ordered[0]] + ([ordered[-1]] if len(ordered) > 1 else [])
            metric = _choose_metric(question, [c for c, s in specs.items() if s["unit"]], specs)
            valued = [r for r in rows if metric and number(r.get(metric)) is not None]
            if valued:
                peak = max(valued, key=lambda r: number(r[metric]))
                if peak not in preview:
                    preview.append(peak)
            intro += "首尾及峰值记录："
    answer = intro + "；".join(_row_text(row, specs) for row in preview) + "。"
    asks_total = any(w in question.lower() for w in ("合计", "总计", "加起来", "总额", "sum"))
    if asks_total and len(rows) > 1:
        for col, total in facts["totals"].items():
            spec = specs[col]
            label = "本次返回行" + spec["label"] + "合计"
            if spec["kind"] == "orders" and (spec["source_table"] in {"ads_top_sellers", "ads_category_rank", "dws_category_summary", "dws_daily_trade"}
                    or any(s["source_column"] in {"seller_id", "category_english"} for s in specs.values())):
                label = "本次返回行关联订单计数之和（非全局去重订单数）"
            answer += f"{label}：{_format_value(total, spec)}。"
    if note := _coverage_note(facts):
        answer += note
    return answer


def _scope_suffix(row_count: int, truncated: bool) -> str:
    if truncated:
        return f"\n\n（结果达到返回上限，仅展示前 {row_count} 行；不能据此计算全量合计，请缩小查询范围）"
    if row_count > 3:
        return f"\n\n（文字仅列部分记录；完整 {row_count} 行查询结果见 rows）"
    return ""


def _decide_response_type(question: str, rows: list, specs: dict | None = None, truncated: bool = False,
                          category_count: int | None = None) -> tuple[str, dict | None]:
    if not rows:
        return "text", None
    specs = specs or output_specs(None, list(rows[0]))
    numeric = [c for c, s in specs.items() if s["unit"] and s["kind"] != "rank"
               and all(number(r.get(c)) is not None for r in rows)]
    metric = _choose_metric(question, numeric, specs)
    if not metric:
        return "table", None
    times = sorted(c for c, s in specs.items() if s["kind"] in {"month", "date"})
    dims = [c for c, s in specs.items() if s["kind"] == "dimension"]
    if any(w in question for w in ("趋势", "每月", "按月", "走势", "每日", "每天", "逐日", "月度")):
        # 多个系列或同一时间重复的行需要分组能力，目前保守展示明细表。
        if (len(times) != 1 or len({str(r.get(times[0])) for r in rows}) != len(rows)
                or any(len({str(r.get(col)) for r in rows}) > 1 for col in dims)):
            return "table", None
        return "chart", {"type": "line", "x": times[0], "y": [metric], "unit": specs[metric]["unit"], "coverage": "returned_rows"}
    identity = [c for c in dims if specs[c]["source_column"] in {"category_english", "seller_id"}]
    labels = identity or times or dims
    if not labels:
        return "table", None
    x = sorted(labels)[0]
    if len({str(row.get(x)) for row in rows}) != len(rows):
        return "table", None
    if _is_rank_question(question) or any(w in question for w in ("占比", "比例", "分布")):
        ratio_cols = [c for c in numeric if specs[c]["kind"] == "percent"]
        ratio = ratio_cols[0] if ratio_cols else None
        complete_categories = (category_count and ratio and specs[ratio]["source_table"] == "ads_category_rank"
                               and specs[ratio]["source_column"] == "gmv_ratio"
                               and specs[x]["source_column"] == "category_english"
                               and specs[x]["source_table"] == "ads_category_rank"
                               and len(rows) == category_count
                               and len({row.get(x) for row in rows}) == category_count
                               and all(Decimal(0) <= number(row.get(ratio)) <= Decimal(100) for row in rows)
                               and abs(sum(number(row.get(ratio)) for row in rows) - Decimal(100)) <= Decimal("0.5"))
        if complete_categories and not truncated and not _is_rank_question(question):
            return "chart", {"type": "pie", "name": x, "value": ratio, "unit": "percent",
                              "denominator": "全局GMV", "coverage": "complete"}
        return "chart", {"type": "bar", "x": x, "y": [metric], "unit": specs[metric]["unit"], "coverage": "returned_rows"}
    return ("table", None) if len(rows) > 3 else ("text", None)


def response_node(state: AgentState) -> AgentState:
    question = state["user_question"]
    rows = state.get("sql_result") or []
    error_type = state.get("sql_error_type")
    if error_type == "unsupported":
        detail = (state.get("sql_error") or "现有授权汇总表不支持该指标").rstrip("。；;.")
        answer = f"当前数据能力无法精确回答：{detail}。"
    elif error_type == "connection":
        answer = "数据库连接失败，无法执行查询。请检查 MySQL 服务是否已启动。"
    elif error_type == "configuration":
        answer = "数据库保护配置未完成，本次查询未执行。请检查只读账号和会话配置。"
    elif error_type == "guard":
        answer = "该查询不符合只读安全策略，请调整问法（仅支持 ADS/DWS 授权表的只读统计）。"
    elif state.get("sql_error"):
        error_type = "sql"
        answer = "查询执行失败，已自动重试仍未成功。请调整问法或稍后重试。"
    elif not rows:
        answer = "查询执行成功，但没有匹配到任何数据。请调整查询条件或数据时间范围后重试。"
    else:
        truncated = bool(state.get("result_truncated"))
        facts = _compute_verified_facts(question, rows, state.get("sql"), state.get("sql_meta"))
        response_type, chart_config = _decide_response_type(question, rows, facts["specs"], truncated,
                                                           facts["coverage"].get("category_count"))
        if chart_config and (note := _coverage_note(facts)):
            chart_config["note"] = note
        answer = _summarize(question, rows, truncated, facts) + _scope_suffix(len(rows), truncated)
        if state.get("schema_warning"):
            answer += f"\n\n（{state['schema_warning']}）"
        return {**state, "final_answer": answer, "response_type": response_type, "chart_config": chart_config, "error_category": None}
    return {**state, "final_answer": answer, "response_type": "text", "chart_config": None, "error_category": error_type}


def chitchat_node(state: AgentState) -> AgentState:
    answer = chat([
        {"role": "system", "content": "你是 DataMind 数据中台的智能助手。简短友好地回答用户，并引导他们查询电商数据。"},
        {"role": "user", "content": state["user_question"]},
    ], temperature=0.7, max_tokens=200)
    return {**state, "final_answer": answer, "response_type": "text", "chart_config": None, "error_category": None}
