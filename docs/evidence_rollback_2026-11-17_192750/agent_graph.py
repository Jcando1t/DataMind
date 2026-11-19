# -*- coding: utf-8 -*-
"""LangGraph Agent 主图
构建 DataMind 智能问数 Agent 的状态机：
意图判断 → Schema检索 → SQL生成 → SQL执行 → 结果校验(重试) → 响应决策
"""

from langgraph.graph import StateGraph, END
from agent.state import AgentState
from agent.nodes.intent import intent_node
from agent.nodes.schema_retrieval import schema_retrieval_node
from agent.nodes.sql_generation import sql_generation_node
from agent.nodes.sql_execution import sql_execution_node
from agent.nodes.response import response_node, chitchat_node
from agent.config import MAX_SQL_RETRIES


# ==================== 条件边：意图路由 ====================
def route_by_intent(state: AgentState) -> str:
    """意图判断后的路由：数据查询 or 闲聊"""
    if state.get("intent") == "data_query":
        return "schema_retrieval"
    return "chitchat"


# ==================== 条件边：SQL 执行结果校验 ====================
def route_after_execution(state: AgentState) -> str:
    """SQL 执行后的路由：
    - 数据库连接失败（重试无意义）→ 直接给出明确报错
    - SQL 语句错误且未超重试次数 → 重新生成
    - 其余情况 → 响应决策
    """
    if state.get("sql_error"):
        # 连接/认证类失败重试也不会成功，直接进入响应
        if state.get("sql_error_type") == "connection":
            return "response"
        retry_count = state.get("retry_count", 0)
        if retry_count < MAX_SQL_RETRIES:
            return "sql_generation"  # 重试
        # 超过重试次数，把错误作为最终答案
        return "response"
    return "response"


# ==================== 重试计数包装 ====================
def increment_retry(state: AgentState) -> AgentState:
    """SQL 执行失败重试时，递增重试计数"""
    return {**state, "retry_count": state.get("retry_count", 0) + 1}


# ==================== 构建图 ====================
def build_graph() -> StateGraph:
    graph = StateGraph(AgentState)

    # 注册节点
    graph.add_node("intent", intent_node)
    graph.add_node("schema_retrieval", schema_retrieval_node)
    graph.add_node("sql_generation", sql_generation_node)
    graph.add_node("sql_execution", sql_execution_node)
    graph.add_node("increment_retry", increment_retry)
    graph.add_node("response", response_node)
    graph.add_node("chitchat", chitchat_node)

    # 入口
    graph.set_entry_point("intent")

    # 意图判断后分叉
    graph.add_conditional_edges(
        "intent",
        route_by_intent,
        {
            "schema_retrieval": "schema_retrieval",
            "chitchat": "chitchat",
        },
    )

    # 主流程
    graph.add_edge("schema_retrieval", "sql_generation")
    graph.add_edge("sql_generation", "sql_execution")

    # SQL 执行后：成功 → response，失败 → increment_retry → sql_generation
    graph.add_conditional_edges(
        "sql_execution",
        route_after_execution,
        {
            "response": "response",
            "sql_generation": "increment_retry",
        },
    )
    graph.add_edge("increment_retry", "sql_generation")

    # 终止节点
    graph.add_edge("response", END)
    graph.add_edge("chitchat", END)

    return graph.compile()


# 单例，供 FastAPI 路由复用
agent_graph = build_graph()
