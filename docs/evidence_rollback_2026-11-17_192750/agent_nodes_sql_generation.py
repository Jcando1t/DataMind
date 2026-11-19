# -*- coding: utf-8 -*-
"""节点3：SQL 生成
根据用户问题 + Schema 上下文，调用 DeepSeek 生成 SQL。
如果上一次执行失败，把错误信息带入 prompt 让 LLM 自我修正。
"""

import re

from agent.llm import chat
from agent.state import AgentState

SYSTEM_PROMPT = """你是一个 MySQL SQL 生成专家。根据用户问题和提供的数据库 Schema，生成准确的 MySQL 查询语句。

规则：
1. 只生成单条 SELECT 查询，禁止 INSERT/UPDATE/DELETE/DROP 等操作，禁止多语句
2. 表名必须使用全限定格式：`库名`.`表名`（例如 `datamind_ads`.`ads_gmv_overview`），库名以 Schema 中标注为准，禁止省略库名，也禁止引用 Schema 之外的表
3. rank 是 MySQL 保留字，使用时必须加反引号：`rank`
4. 金额字段（gmv、total_gmv 等）保留两位小数：ROUND(field, 2)
5. 结果默认限制 100 行：加 LIMIT 100
6. 只输出 SQL 语句本身，不要有任何解释、markdown 代码块标记
7. 只使用 Schema 中明确列出的字段名，不要猜测或发明字段名
8. year_month 是 MySQL 保留字，使用时必须加反引号：`year_month`
9. 禁止 SELECT ... INTO、FOR UPDATE，禁止访问系统库和非授权表
10. 如果找不到合适的表或字段，输出：SELECT '未找到相关数据' AS message"""


def _build_messages(state: AgentState) -> list[dict]:
    """构建发给 LLM 的消息列表"""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    # 加入历史对话（最近 4 轮，避免 token 过多），支持"那前三名呢？"这类追问
    for turn in state.get("chat_history", [])[-8:]:
        messages.append(turn)

    # 构建用户消息
    user_content = f"""可用的数据表：
{state['schema_context']}

用户问题：{state['user_question']}"""

    # 如果是重试，把上次的错误附上
    if state.get("sql_error") and state.get("retry_count", 0) > 0:
        user_content += f"""

上次生成的 SQL 执行报错：
{state['sql_error']}

请修正 SQL，避免上述错误。"""

    messages.append({"role": "user", "content": user_content})
    return messages


def _extract_sql(raw: str) -> str:
    """从 LLM 输出中提取纯 SQL

    只去掉 markdown 代码块标记，**不按分号切分**——
    字符串字面量里的分号（如 SELECT 'a;b' AS note）必须保留。
    真正的多语句输入交给执行层的结构校验拒绝，并给出明确报错。
    """
    text = re.sub(r"```(?:sql)?", "", raw or "", flags=re.IGNORECASE)
    text = text.strip().strip("`").strip()
    return text


def sql_generation_node(state: AgentState) -> AgentState:
    """生成或修正 SQL"""
    messages = _build_messages(state)
    raw_sql = chat(messages, temperature=0, max_tokens=512)
    sql = _extract_sql(raw_sql)

    return {**state, "sql": sql, "sql_error": None}
