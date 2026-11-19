# -*- coding: utf-8 -*-
"""智能问数路由：请求校验、错误分类与完整响应脱敏。"""
from __future__ import annotations
import logging
import os
import sys
from typing import Any, Dict, Literal, Optional
from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from agent.graph import agent_graph
from agent.llm import LLMError
from agent.nodes.schema_retrieval import SchemaRetrievalError
from agent.redaction import sanitize_data, sanitize_text

router = APIRouter()
logger = logging.getLogger("datamind")
ERROR_STATUS = {
    "connection": (503, "数据库暂时不可用，请稍后重试"),
    "guard": (400, "查询被安全策略拒绝，请换一种问法"),
    "sql": (500, "查询执行失败，请调整问法后重试"),
    "configuration": (500, "数据库保护配置未完成，请检查服务配置"),
    "unsupported": (422, "现有汇总数据无法精确回答该问题"),
    "review_index": (503, "评论索引暂时不可用，请稍后重试"),
}


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2000)

    @field_validator("content")
    @classmethod
    def trim_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("历史消息不能为空")
        return value


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=500)
    chat_history: list[ChatMessage] = Field(default_factory=list, max_length=20)

    @field_validator("question")
    @classmethod
    def trim_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("问题不能为空白")
        return value


class ChatResponse(BaseModel):
    code: int = 200
    message: str = "success"
    data: Optional[Dict[str, Any]] = None


def _sanitize(text: str) -> str:
    return sanitize_text(text)


def _payload(result: dict) -> dict:
    rows = result.get("sql_result") or []
    return sanitize_data({"answer": result.get("final_answer") or "",
        "response_type": result.get("response_type", "text"), "chart_config": result.get("chart_config"),
        "sql": result.get("sql"), "rows": rows, "row_count": len(rows),
        "result_truncated": bool(result.get("result_truncated")), "error_category": result.get("error_category"),
        "review_insight": result.get("review_insight"), "sql_error": result.get("sql_error")})


def _error_response(status: int, message: str, data: dict | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": status, "message": message, "data": data})


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    initial_state = {"user_question": req.question, "chat_history": [m.model_dump() for m in req.chat_history],
        "intent": None, "schema_context": None, "schema_status": None, "schema_warning": None,
        "sql": None, "sql_result": None, "sql_error": None, "sql_error_type": None,
        "retry_count": 0, "result_truncated": False, "sql_meta": None,
        "final_answer": None, "response_type": None, "chart_config": None, "review_insight": None,
        "error_category": None}
    try:
        # 同步图放在线程池，避免阻塞本服务 Schema 回调。
        result = await run_in_threadpool(agent_graph.invoke, initial_state)
    except SchemaRetrievalError:
        logger.error("数据字典服务调用失败")
        return _error_response(503, "数据字典服务暂时不可用，请稍后重试")
    except LLMError as exc:
        logger.error("模型调用失败：%s", sanitize_text(str(exc)))
        return _error_response(503, "模型服务暂时不可用，请稍后重试")
    except Exception as exc:
        logger.error("Agent 未处理异常 %s：%s", type(exc).__name__, sanitize_text(str(exc)))
        return _error_response(500, "智能问数服务暂时不可用，请稍后重试")
    category = result.get("error_category")
    if not category:
        return ChatResponse(data=_payload(result))
    status, message = ERROR_STATUS.get(category, (500, "查询执行失败"))
    return _error_response(status, message, _payload(result))
