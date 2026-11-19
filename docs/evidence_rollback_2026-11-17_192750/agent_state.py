# -*- coding: utf-8 -*-
"""LangGraph Agent 状态定义"""

from typing import Any, Dict, List, Optional
from typing_extensions import TypedDict


class AgentState(TypedDict):
    """贯穿整个 Agent 图的状态对象，每个节点读取并更新它

    注意：chat_history 用普通覆盖语义（不用 operator.add 累加器）。
    因为每个节点返回 {**state, ...} 完整状态，若用累加器，
    历史会在每经过一个节点时翻倍，多轮对话时严重膨胀。
    历史由调用方（chat.py 的请求参数）负责维护和传入。
    """

    # 用户输入
    user_question: str

    # 对话历史（支持多轮追问）
    chat_history: List[Dict[str, str]]

    # 意图判断结果：data_query | chitchat
    intent: Optional[str]

    # Schema 检索结果：匹配到的表和字段信息
    schema_context: Optional[str]

    # 生成的 SQL
    sql: Optional[str]

    # SQL 执行结果（行列表）
    sql_result: Optional[List[Dict[str, Any]]]

    # SQL 执行错误信息（用于重试）
    sql_error: Optional[str]

    # 错误类型：connection=数据库连接失败（重试无意义）| sql=SQL 语句错误（可重试）
    #          guard=被只读安全策略拒绝（可通过重新生成 SQL 修正）
    sql_error_type: Optional[str]

    # 当前 SQL 重试次数
    retry_count: int

    # 结果是否因超过单次返回上限而被截断
    result_truncated: bool

    # SQL 执行元信息（引用到的表、LIMIT 处理方式）
    sql_meta: Optional[Dict[str, Any]]

    # 最终回复给用户的内容
    final_answer: Optional[str]

    # 响应类型：text | table | chart
    response_type: Optional[str]

    # 图表建议（response_type=chart 时有值）
    chart_config: Optional[Dict[str, Any]]

    # 错误类别（供 API 判定业务状态）：connection | guard | sql | None（成功）
    error_category: Optional[str]