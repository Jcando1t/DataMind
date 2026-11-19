# -*- coding: utf-8 -*-
"""Agent 对话路由
POST /api/chat  接收用户问题，触发 LangGraph Agent，返回结构化结果

状态码约定（前端可据此区分成功与故障，而不是只看 200）：
- 200 code=200 正常结果，或"查询成功但没有匹配数据"的成功空结果
- 400 code=400 查询被只读安全策略拒绝
- 500 code=500 SQL 执行失败（含重试耗尽）
- 503 code=503 数据库连接失败（MySQL 不可用）
"""

import os
import sys

# 把项目根目录加入 path，让 backend 能导入 agent 模块
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Any, Dict, List, Optional

from agent.config import MYSQL_QUERY_PASSWORD
from agent.graph import agent_graph

router = APIRouter()

# 错误类别 → (HTTP 状态码, 业务 code, 对外提示)
# 对外提示不包含任何内部细节（堆栈、凭据、SQL 文本），细节只放在日志里
ERROR_STATUS: dict[str, tuple[int, str]] = {
    "connection": (503, "数据库暂时不可用，请稍后重试"),
    "guard": (400, "查询被安全策略拒绝，请换一种问法"),
    "sql": (500, "查询执行失败，请调整问法后重试"),
}


class ChatRequest(BaseModel):
    question: str
    chat_history: Optional[List[Dict[str, str]]] = []


class ChatResponse(BaseModel):
    code: int = 200
    message: str = "success"
    data: Optional[Dict[str, Any]] = None


def _sanitize(text: str) -> str:
    """脱敏：确保任何凭据都不会出现在响应里"""
    if not text:
        return text
    safe = text
    for secret in (MYSQL_QUERY_PASSWORD, os.getenv("DEEPSEEK_API_KEY", "")):
        if secret and len(secret) >= 6:
            safe = safe.replace(secret, "***")
    return safe


def _payload(result: dict) -> dict:
    """组装返回给前端的结构化数据"""
    rows = result.get("sql_result") or []
    return {
        "answer": _sanitize(result.get("final_answer") or ""),
        "response_type": result.get("response_type", "text"),
        "chart_config": result.get("chart_config"),
        "sql": result.get("sql"),
        "rows": rows,
        "row_count": len(rows),
        "result_truncated": bool(result.get("result_truncated")),
        "error_category": result.get("error_category"),
    }


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """智能问数接口：自然语言 → SQL → 结果 + 图表建议"""
    # 初始化 Agent 状态
    initial_state = {
        "user_question": req.question,
        "chat_history": req.chat_history or [],
        "intent": None,
        "schema_context": None,
        "sql": None,
        "sql_result": None,
        "sql_error": None,
        "sql_error_type": None,
        "retry_count": 0,
        "result_truncated": False,
        "sql_meta": None,
        "final_answer": None,
        "response_type": None,
        "chart_config": None,
        "error_category": None,
    }

    try:
        # agent_graph.invoke 是同步阻塞调用（内部含同步 LLM/HTTP 请求），
        # 必须放入线程池执行，否则会卡住事件循环，
        # 导致 Agent 内部回调本服务 /api/schema/search 的请求无法被处理而超时。
        result = await run_in_threadpool(agent_graph.invoke, initial_state)
    except Exception as e:  # noqa: BLE001 - 图执行异常统一按服务端错误返回
        return JSONResponse(
            status_code=500,
            content={
                "code": 500,
                "message": _sanitize(f"Agent 执行失败：{e}"),
                "data": None,
            },
        )

    category = result.get("error_category")
    if not category:
        return ChatResponse(data=_payload(result))

    status_code, message = ERROR_STATUS.get(category, (500, "查询执行失败"))
    return JSONResponse(
        status_code=status_code,
        content={
            "code": status_code,
            "message": message,
            "data": _payload(result),
        },
    )
