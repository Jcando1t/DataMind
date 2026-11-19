# -*- coding: utf-8 -*-
"""节点2：Schema 检索
从 FastAPI 数据字典接口检索与用户问题相关的表和字段，
构建精简的 schema_context 传给 SQL 生成节点。

设计要点：
- 检索异常不再静默吞掉：单关键词失败记录告警，全部失败时抛出明确异常，
  由 chat.py 返回清晰的错误信息，避免"拿着空 Schema 生成兜底 SQL"的假成功。
- 关键词提取支持"前N名/前N大"等排行类表达；
  当前问题提取不到关键词时，回退用历史里最近一条用户消息提取（支持追问场景）。
"""

import re

import httpx
from agent.config import BACKEND_URL
from agent.state import AgentState


def _extract_keywords(question: str) -> list[str]:
    """从问题中提取检索关键词，映射到数据字典可匹配的英文词"""
    keyword_map = {
        "gmv": "GMV", "成交": "GMV", "销售额": "GMV", "销售": "GMV",
        "营收": "GMV", "收入": "GMV", "金额": "GMV",
        "订单": "order", "下单": "order", "单量": "order",
        "用户": "user", "活跃": "active",
        "品类": "category", "类目": "category", "品种": "category",
        "卖家": "seller", "商家": "seller", "商户": "seller",
        "趋势": "trend", "月": "trend", "环比": "trend", "按月": "trend", "月份": "trend",
        "排行": "rank", "排名": "rank", "最高": "rank", "最多": "rank",
        "客单价": "avg_order_value", "均价": "avg_order_value",
    }
    keywords = set()
    for zh, en in keyword_map.items():
        if zh in question or zh in question.lower():
            keywords.add(en)
    # "前N名/前N大/前N个"属于排行类表达（如"那前三名呢？"），N 支持阿拉伯和中文数字
    if re.search(r"前\s*[0-9一二三四五六七八九十百]+\s*[名大个]", question):
        keywords.add("rank")
    return list(keywords)


def _format_schema(tables: list[dict]) -> str:
    """把检索到的表信息格式化为对 LLM 友好的文本"""
    if not tables:
        return "未找到相关数据表。"
    lines = []
    for t in tables:
        lines.append(f"表名: {t['table_name']} (数据库: {t['database']})")
        lines.append(f"描述: {t['table_desc']}")
        lines.append("字段:")
        for f in t.get("fields", []):
            lines.append(f"  - {f['name']}: {f['desc']}")
        lines.append("")
    return "\n".join(lines)


def schema_retrieval_node(state: AgentState) -> AgentState:
    """检索与用户问题相关的数据字典"""
    question = state["user_question"]
    keywords = _extract_keywords(question)

    # 追问场景：当前问题提取不到关键词时，用历史里最近一条用户消息补充提取
    if not keywords:
        for turn in reversed(state.get("chat_history") or []):
            if turn.get("role") == "user" and turn.get("content"):
                keywords = _extract_keywords(turn["content"])
                if keywords:
                    break

    # 仍然没有关键词时，用问题原文检索（保持原有兜底行为）
    if not keywords:
        keywords = [question[:20]]

    # 对每个关键词检索，合并去重
    seen_tables = set()
    all_tables = []
    failures = []  # 记录每个关键词的失败原因，便于定位
    with httpx.Client(timeout=10) as client:
        for kw in keywords[:3]:  # 最多用 3 个关键词，避免结果过多
            try:
                resp = client.get(
                    f"{BACKEND_URL}/api/schema/search",
                    params={"q": kw},
                )
                if resp.status_code == 200:
                    data = resp.json().get("data", [])
                    for table in data:
                        tname = table["table_name"]
                        if tname not in seen_tables:
                            seen_tables.add(tname)
                            all_tables.append(table)
                else:
                    failures.append(f"关键词'{kw}' 请求返回 {resp.status_code}")
            except Exception as e:
                # 单个关键词失败不阻断整体流程，但必须记录，不许静默吞掉
                failures.append(f"关键词'{kw}' 请求异常: {e}")

    # 所有关键词都失败说明 Schema 服务不可用，属于基础设施故障，
    # 必须抛出明确异常（由 chat.py 返回清晰错误），而不是拿空 Schema 继续
    if failures and not all_tables:
        raise RuntimeError(f"Schema 检索接口调用失败：{'; '.join(failures)}")

    schema_context = _format_schema(all_tables)
    return {**state, "schema_context": schema_context}