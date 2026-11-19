# -*- coding: utf-8 -*-
"""节点2：稳定优先级检索，完整校验上游结构并区分空匹配与服务失败。"""
from __future__ import annotations
import logging
import re
import httpx
from agent.config import BACKEND_URL
from agent.result_semantics import time_grain
from agent.state import AgentState

logger = logging.getLogger("datamind.agent")


class SchemaRetrievalError(RuntimeError):
    """数据字典不可用时不继续生成 SQL。"""


def _extract_keywords(question: str) -> list[str]:
    q = (question or "").lower()
    keywords = []
    def add(word):
        if word not in keywords:
            keywords.append(word)
    daily = time_grain(q) in {"day", "daily"} or "日活" in q
    if any(w in q for w in ("用户", "客户", "user", "customer")):
        if daily or "活跃" in q:
            add("active_user_count")
        if not daily and any(w in q for w in ("多少", "几", "去重", "独立", "总", "累计")):
            add("total_customers")
        add("user")
        add("customer")
    mapping = [
        (("gmv", "成交", "销售额", "销售", "营收", "收入", "金额"), "GMV"),
        (("订单", "下单", "单量", "order"), "order"),
        (("品类", "类目", "品种", "category"), "category"),
        (("卖家", "商家", "商户", "seller"), "seller"),
        (("趋势", "按月", "月份", "环比", "走势", "每月"), "trend"),
        (("排行", "排名", "最高", "最多", "top"), "rank"),
        (("客单价", "均价"), "avg_order_value"),
    ]
    for triggers, keyword in mapping:
        if any(t in q for t in triggers):
            add(keyword)
    if re.search(r"前\s*[0-9一二三四五六七八九十百]+\s*[名大个]", q):
        add("rank")
    return keywords


def _valid_table(item) -> bool:
    return (isinstance(item, dict)
            and all(isinstance(item.get(k), str) and bool(item[k].strip()) for k in ("table_name", "database", "table_desc"))
            and isinstance(item.get("fields"), list)
            and all(isinstance(f, dict) and all(isinstance(f.get(k), str) for k in ("name", "desc")) for f in item["fields"]))


def _format_schema(tables: list[dict]) -> str:
    if not tables:
        return "未找到相关数据表。"
    lines = []
    for t in tables:
        lines.extend([f"表名: {t['table_name']} (数据库: {t['database']})", f"描述: {t['table_desc']}", "字段:"])
        lines.extend(f"  - {f['name']}: {f['desc']}" for f in t["fields"])
        lines.append("")
    return "\n".join(lines)


def schema_retrieval_node(state: AgentState) -> AgentState:
    question = state["user_question"]
    keywords = _extract_keywords(question)
    if not keywords:
        for turn in reversed(state.get("chat_history") or []):
            if turn.get("role") == "user":
                keywords = _extract_keywords(turn.get("content", ""))
                if keywords:
                    break
    keywords = keywords or [question[:20]]
    successes, failures = 0, 0
    hits = {}
    with httpx.Client(timeout=10) as client:
        for keyword in keywords:
            try:
                response = client.get(f"{BACKEND_URL}/api/schema/search", params={"q": keyword})
                if response.status_code != 200:
                    failures += 1
                    continue
                body = response.json()
                if not isinstance(body, dict) or body.get("code") != 200 or not isinstance(body.get("data"), list) or not all(_valid_table(t) for t in body["data"]):
                    failures += 1
                    continue
                successes += 1
                for table in body["data"]:
                    hits.setdefault((table["database"], table["table_name"]), table)
            except (httpx.HTTPError, ValueError, TypeError):
                failures += 1
    if successes == 0:
        logger.error("Schema 检索全部失败，共 %d 个请求", failures)
        raise SchemaRetrievalError("数据字典服务暂时不可用，请稍后重试")
    if failures:
        logger.warning("Schema 检索部分失败：成功 %d，失败 %d", successes, failures)
    return {**state, "schema_context": _format_schema(list(hits.values())),
            "schema_status": "partial" if failures else ("matched" if hits else "empty"),
            "schema_warning": "部分数据字典关键词检索失败，已使用可用结果继续" if failures else None}

