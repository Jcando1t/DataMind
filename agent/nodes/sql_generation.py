# -*- coding: utf-8 -*-
"""节点3：保留 SQL 正文，精确区分不支持的指标与可查询的逐日指标。"""
from __future__ import annotations

import re
from agent.llm import chat
from agent.redaction import sanitize_text
from agent.result_semantics import time_grain
from agent.state import AgentState
from backend.data_dictionary import DATA_PERIOD_START, DATA_PERIOD_END
from datamind_config import load_dataset_config

_DATASET = load_dataset_config()
SYSTEM_PROMPT = f"""你是 MySQL 查询专家。根据用户问题和提供的 Schema 生成准确查询。
规则：
1. 只输出单条 SELECT，禁止写入、多语句、INTO、加锁读、系统库和非授权表；只使用 Schema 字段。
2. 表名使用全限定库名.表名；rank、year_month 等标识符使用反引号。
3. 金额单位{_DATASET['currency']}；GMV口径为{_DATASET['gmv_definition']}，金额保留两位小数。
4. 排行默认 LIMIT 100；趋势保留完整序列，不主动 LIMIT 100，执行层限制最多1000行。
5. 总客户数读取 ads_gmv_overview.total_customers，是全周期去重下单客户，不是登录人数。
6. dws_daily_active_users 是每日去重下单人数。指定单日、筛选某年后逐日展示均可查询；跨日人数之和是用户日人次，不是期间去重客户数。不能因年份限定拒绝逐日序列。
7. 全局订单读 ads_gmv_overview.total_orders，月度总体订单读 ads_gmv_trend.monthly_orders。品类/卖家关联订单不可相加冒充全局去重订单。
8. mom_growth_rate 和 gmv_ratio 已是百分数，不再乘100。
9. 最近一期按用户所限定范围的最大时间，不是当前日历月份。实际覆盖日期见 overview.data_period_start/data_period_end，不能将末月覆盖说明套到历史月。
10. 跨日时间段的精确去重客户总数无法由现有表推导。ads_top_sellers 只有全周期按GMV的Top20，不支持全体卖家订单榜或历史卖家榜。
11. 无法准确回答时，只输出 UNSUPPORTED: 简短原因。不要输出占位 SELECT。
12. 尽量保留标准字段名；别名不能改变业务含义。只查用户需要的字段，不增加无关金额或合计。
13. 只输出 SQL 或 UNSUPPORTED，不解释，不使用 Markdown 围栏。
"""
if DATA_PERIOD_START and DATA_PERIOD_END:
    SYSTEM_PROMPT += f"\n默认样例快照覆盖{DATA_PERIOD_START}至{DATA_PERIOD_END}，实际以表内覆盖日期为准。"
_PLACEHOLDER_RE = re.compile(r"^\s*SELECT\s+'未找到相关数据'\s+AS\s*`?message`?\s*;?\s*$", re.I)


def _unsupported_reason(question: str, schema_context: str | None) -> str | None:
    if not schema_context or "未找到相关数据表" in schema_context:
        return "现有数据字典中没有匹配到可回答该问题的授权汇总表"
    q = question.lower()
    distinct = any(w in q for w in ("去重", "独立", "不重复", "distinct")) and any(w in q for w in ("用户", "客户", "customer", "user"))
    period = bool(re.search(r"\d{4}\s*年|\d{4}[-/]\d{1,2}|时间段|期间|区间|以来|之前|之后", q))
    if distinct and period and time_grain(q) == "period":
        return "现有授权汇总表只有全周期去重客户数和每日去重下单用户数，无法精确推导自定义期间的去重客户数；每日用户数相加是人次而非去重人数"
    return None


def _unsupported_state(state: AgentState, reason: str) -> AgentState:
    return {**state, "sql": None, "sql_result": None, "sql_error": reason,
            "sql_error_type": "unsupported", "result_truncated": False, "sql_meta": None}


def _build_messages(state: AgentState) -> list[dict]:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(state.get("chat_history", [])[-8:])
    content = f"可用的数据表：\n{state['schema_context']}\n用户问题：{state['user_question']}"
    if state.get("sql_error") and state.get("retry_count", 0) > 0:
        content += f"\n上次SQL错误（已脱敏）：{sanitize_text(state['sql_error'])}\n请修正SQL。"
    messages.append({"role": "user", "content": content})
    return messages


def _extract_sql(raw: str) -> str:
    """仅去除最外围格式，不修改 SQL 中的反引号、分号或字符串。"""
    text = (raw or "").strip()
    fenced = re.fullmatch(r"```(?:sql|mysql|text)?[^\S\r\n]*\r?\n?(.*?)\r?\n?```", text, re.I | re.S)
    return fenced.group(1).strip() if fenced else text


def sql_generation_node(state: AgentState) -> AgentState:
    reason = _unsupported_reason(state["user_question"], state.get("schema_context"))
    if reason:
        return _unsupported_state(state, reason)
    sql = _extract_sql(chat(_build_messages(state), temperature=0, max_tokens=512))
    if sql.upper().startswith("UNSUPPORTED:"):
        return _unsupported_state(state, sql.split(":", 1)[1].strip() or "现有汇总表无法准确回答")
    if _PLACEHOLDER_RE.fullmatch(sql):
        return _unsupported_state(state, "现有授权汇总表无法准确回答该问题，请调整指标或时间范围")
    return {**state, "sql": sql, "sql_error": None, "sql_error_type": None}

