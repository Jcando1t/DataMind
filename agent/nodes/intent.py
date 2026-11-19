# -*- coding: utf-8 -*-
"""节点1：意图判断
判断用户输入是数据查询还是闲聊，避免把所有问题都走 SQL 路径。
结合对话历史判断，确保"那前三名呢？"这类追问能正确进入查询路径。
"""

import re

from agent.llm import chat
from agent.state import AgentState

REVIEW_TERMS = (
    "评论", "评价", "review", "反馈", "差评", "好评", "抱怨", "投诉", "低分", "高分",
    "评分", "一星", "二星", "两星", "三星", "四星", "五星", "评论原文", "用户怎么说",
)
_FOLLOWUP = re.compile(r"^(?:那|那么|其中|这些|这类|还有|再|继续)|(?:方面|原因|呢？?)$")


def is_review_question(question: str, history: list[dict] | None = None) -> bool:
    """明显的评论问题直接路由，避免分类模型把“差评”误判成普通指标。"""
    text = (question or "").lower()
    if any(term in text for term in REVIEW_TERMS):
        return True
    if _FOLLOWUP.search(text):
        return any(
            any(term in (turn.get("content", "").lower()) for term in REVIEW_TERMS)
            for turn in (history or [])[-4:]
        )
    return False


SYSTEM_PROMPT = """你是一个意图分类器。判断用户的问题属于电商指标查询、评论洞察还是闲聊。

规则：
- 如果问题涉及评论、评价、评分、差评、好评、用户反馈、抱怨原因或评论原文 → 回复 review_query
- 如果问题涉及 GMV、订单、用户、品类、卖家、销售额、趋势、排行、环比等数据指标 → 回复 data_query
- 对上一轮问题的追问或指代要结合历史，继承上一轮意图
- 其他问题（打招呼、闲聊、问你是谁等）→ 回复 chitchat

只回复 data_query、review_query 或 chitchat，不要有任何其他内容。"""


def intent_node(state: AgentState) -> AgentState:
    """判断用户意图（结合最近历史，支持追问场景）"""
    # 把最近几轮历史拼进判断输入，让追问类问题能正确归类
    history = state.get("chat_history") or []
    if is_review_question(state["user_question"], history):
        return {**state, "intent": "review_query"}
    history_lines = [
        f"{t.get('role', 'user')}: {t.get('content', '')}" for t in history[-4:]
    ]
    history_text = "\n".join(history_lines) if history_lines else "（无历史）"

    user_content = f"""最近的对话历史（可能为空）：
{history_text}

当前问题：{state["user_question"]}"""

    intent = chat(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        temperature=0,
        max_tokens=16,
    ).strip()
    if intent not in ("data_query", "review_query", "chitchat"):
        intent = "data_query"  # 不确定时默认走查询路径

    return {**state, "intent": intent}