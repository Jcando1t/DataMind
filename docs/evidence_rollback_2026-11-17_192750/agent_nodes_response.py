# -*- coding: utf-8 -*-
"""节点5：响应决策
根据查询结果，决定返回纯文字、表格还是图表建议，
并调用 DeepSeek 生成自然语言总结。

明确区分四种情况，避免一律回复"未查询到相关数据"：
1. 数据库连接失败 → 明确提示检查 MySQL 服务，error_category=connection
2. SQL 被安全策略拒绝 → 明确说明拒绝原因，error_category=guard
3. SQL 执行失败（重试耗尽）→ 明确给出错误原因，error_category=sql
4. 执行成功但结果为空 → 成功空结果，error_category=None（API 仍返回成功）
"""

import json
import re

from agent.llm import chat
from agent.sql_guard import MAX_ROWS
from agent.state import AgentState

# 结果行数在此范围内时，完整序列交给总结模型
# （月度趋势 25 行、品类排行 72 行、卖家 Top20 都属于此类，必须全量给模型）
FULL_SERIES_MAX_ROWS = 100
# 超过上限时的抽样：首尾各取若干行 + 极值行
SAMPLE_HEAD = 10
SAMPLE_TAIL = 10

# 金额口径统一说明（数据源为巴西电商数据）
CURRENCY_RULE = (
    "所有金额字段的单位是巴西雷亚尔（BRL），写作 R$ 或 BRL，"
    "禁止称为“元”“人民币”或“¥”。"
)


def _is_numeric_column(rows: list, col: str) -> bool:
    """判断某列是否为数值列（用于自动识别图表的维度列和度量列）"""
    values = [r.get(col) for r in rows if r.get(col) is not None]
    if not values:
        return False
    return all(isinstance(v, (int, float)) for v in values)


def _decide_response_type(question: str, rows: list) -> tuple[str, dict | None]:
    """根据问题和结果决定响应类型，返回 (response_type, chart_config)"""
    if not rows:
        return "text", None

    q = question.lower()
    col_names = list(rows[0].keys())

    # 数值列 / 维度列自动识别：维度列（分类轴）取第一个非数值列，
    # 避免把 rank 序号当成 x 轴；rank 之外的数值列作为度量
    numeric_cols = [c for c in col_names if _is_numeric_column(rows, c)]
    label_cols = [c for c in col_names if c not in numeric_cols]
    value_cols = [c for c in numeric_cols if c != "rank"]

    x_axis = label_cols[0] if label_cols else col_names[0]
    y_axis = value_cols if value_cols else [c for c in col_names if c != x_axis]

    # 趋势类 → 折线图（按问题里的度量词挑单一口径，避免同轴混单位）
    if any(w in q for w in ("趋势", "每月", "月份", "按月", "走势")):
        prefer: list[str] = []
        if any(w in q for w in ("gmv", "销售额", "成交", "金额")):
            prefer = [c for c in value_cols if "gmv" in c.lower()]
        elif "订单" in q or "单量" in q:
            prefer = [c for c in value_cols if "order" in c.lower()]
        return "chart", {"type": "line", "x": x_axis, "y": prefer or y_axis}

    # 排行/占比/前N类 → 柱状图或饼图
    is_rank_like = bool(
        re.search(r"前\s*[0-9一二三四五六七八九十百]+\s*[名大个]", question)
        or any(w in q for w in ("排行", "排名", "top", "占比", "占", "分布"))
    )
    if is_rank_like:
        pie_value_cols = [c for c in value_cols if "ratio" in c.lower()]
        if len(rows) <= 10 and pie_value_cols:
            return "chart", {
                "type": "pie",
                "name": x_axis,
                "value": pie_value_cols[0],
            }
        return "chart", {"type": "bar", "x": x_axis, "y": y_axis}

    # 多行数据 → 表格
    if len(rows) > 3:
        return "table", None

    # 少量数据 → 文字
    return "text", None


def _sample_rows(rows: list) -> tuple[list, str]:
    """大结果集的分层抽样：首尾各取若干行 + 极值行

    返回 (抽样行, 给模型的说明)。抽样时必须让模型知道范围，避免以偏概全。
    """
    if len(rows) <= FULL_SERIES_MAX_ROWS:
        return rows, ""  # 全量交给模型

    head = rows[:SAMPLE_HEAD]
    tail = rows[-SAMPLE_TAIL:]
    picked_indices = list(range(len(head))) + list(range(len(rows) - len(tail), len(rows)))

    # 追加极值行（按第一个数值列的最大/最小行）
    numeric_cols = [c for c in rows[0] if _is_numeric_column(rows, c)] if rows else []
    if numeric_cols:
        col = numeric_cols[0]
        valued = [r for r in rows if isinstance(r.get(col), (int, float))]
        if valued:
            top = max(valued, key=lambda r: r[col])
            bottom = min(valued, key=lambda r: r[col])
            for row in (top, bottom):
                idx = rows.index(row)
                if idx not in picked_indices:
                    picked_indices.append(idx)

    sample = [rows[i] for i in picked_indices]
    note = (
        f"注意：本次查询共返回 {len(rows)} 行，为控制篇幅只提供了其中 {len(sample)} 行"
        f"（前 {SAMPLE_HEAD} 行、后 {SAMPLE_TAIL} 行以及最大值/最小值行）。"
        "回答时必须说明这是基于部分数据的观察，不能把抽样结果描述为全部数据。"
    )
    return sample, note


def _summarize(question: str, rows: list, truncated: bool = False) -> str:
    """调用 DeepSeek 生成自然语言总结（有限序列用全量，大结果集显式说明抽样范围）"""
    sample, scope_note = _sample_rows(rows)
    data_str = json.dumps(sample, ensure_ascii=False, indent=2)

    extra = ""
    if scope_note:
        extra += f"\n{scope_note}"
    if truncated:
        extra += "\n注意：结果超过单次返回上限，仅取回前 1000 行，回答时不要把它当作全部数据。"

    prompt = f"""用户问题：{question}

查询结果（共 {len(rows)} 条{("，已抽样 %d 条" % len(sample)) if scope_note else ""}）：
{data_str}

要求：
1. 用简洁的中文回答用户的问题，直接给出关键数字和结论，不超过 150 字。
2. {CURRENCY_RULE}
3. 如果结果包含完整的时间序列，必须覆盖首尾两端（尤其是最新一期）再做整体判断，不能只描述前半段。{extra}
不要重复用户的问题。"""

    return chat(
        [{"role": "user", "content": prompt}], temperature=0.3, max_tokens=400
    )


def _scope_suffix(row_count: int, truncated: bool) -> str:
    """给用户的可见范围提示（仅在数据被截断/抽样时追加）"""
    if truncated:
        return f"\n\n（结果较多，仅展示前 {MAX_ROWS} 行；如需完整数据请缩小查询范围）"
    if row_count > FULL_SERIES_MAX_ROWS:
        return f"\n\n（共 {row_count} 行结果，以上结论基于首尾抽样与极值行）"
    return ""


def response_node(state: AgentState) -> AgentState:
    """生成最终响应，区分连接失败 / 安全拒绝 / SQL 失败 / 空结果 / 正常结果"""
    question = state["user_question"]
    rows = state.get("sql_result") or []
    truncated = bool(state.get("result_truncated"))

    # 情况 1：数据库连接失败 —— 明确提示是环境问题，不是查询问题
    if state.get("sql_error_type") == "connection":
        return {
            **state,
            "final_answer": "数据库连接失败，无法执行查询。请检查 MySQL 服务是否已启动。",
            "response_type": "text",
            "chart_config": None,
            "error_category": "connection",
        }

    # 情况 2：被只读安全策略拒绝 —— 说明理由，便于修正问法
    if state.get("sql_error_type") == "guard":
        return {
            **state,
            "final_answer": f"该查询被安全策略拒绝：{state.get('sql_error')}。请换一种问法（仅支持查询 ADS/DWS 授权表的只读统计）。",
            "response_type": "text",
            "chart_config": None,
            "error_category": "guard",
        }

    # 情况 3：SQL 执行失败且重试耗尽 —— 给出具体错误，方便排查
    if state.get("sql_error"):
        return {
            **state,
            "final_answer": f"SQL 执行失败（已自动重试仍未成功）：{state.get('sql_error')}",
            "response_type": "text",
            "chart_config": None,
            "error_category": "sql",
        }

    # 情况 4：执行成功但结果为空 —— 与前几种故障明确区分（属成功空结果）
    if not rows:
        return {
            **state,
            "final_answer": "查询执行成功，但没有匹配到任何数据。可能是查询条件与数据内容不匹配，请调整问法或缩小时间范围后重试。",
            "response_type": "text",
            "chart_config": None,
            "error_category": None,
        }

    # 情况 5：正常结果
    response_type, chart_config = _decide_response_type(question, rows)
    summary = _summarize(question, rows, truncated=truncated)
    return {
        **state,
        "final_answer": summary + _scope_suffix(len(rows), truncated),
        "response_type": response_type,
        "chart_config": chart_config,
        "error_category": None,
    }


def chitchat_node(state: AgentState) -> AgentState:
    """处理非数据查询的闲聊"""
    answer = chat(
        [
            {
                "role": "system",
                "content": "你是 DataMind 数据中台的智能助手。简短友好地回答用户，并引导他们查询电商数据。",
            },
            {"role": "user", "content": state["user_question"]},
        ],
        temperature=0.7,
        max_tokens=200,
    )
    return {
        **state,
        "final_answer": answer,
        "response_type": "text",
        "chart_config": None,
        "error_category": None,
    }
