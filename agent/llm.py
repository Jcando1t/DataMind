# -*- coding: utf-8 -*-
"""模型客户端：凭据懒加载、有界超时/重试、截断输出拒绝。"""
from __future__ import annotations

import httpx
from openai import APIConnectionError, APITimeoutError, OpenAI, OpenAIError

from agent.config import (
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    LLM_CONNECT_TIMEOUT,
    LLM_MAX_RETRIES,
    LLM_READ_TIMEOUT,
    require_api_key,
)

_client: OpenAI | None = None


class LLMError(RuntimeError):
    """模型调用失败；对外信息固定且不携带第三方原始错误。"""


class LLMOutputError(LLMError):
    """模型响应为空或被 max_tokens 截断。"""


def get_client() -> OpenAI:
    """获取模型客户端（首次调用时按需创建）。"""
    global _client
    if _client is None:
        timeout = httpx.Timeout(
            connect=LLM_CONNECT_TIMEOUT,
            read=LLM_READ_TIMEOUT,
            write=LLM_CONNECT_TIMEOUT,
            pool=LLM_CONNECT_TIMEOUT,
        )
        _client = OpenAI(
            api_key=require_api_key(),
            base_url=DEEPSEEK_BASE_URL,
            timeout=timeout,
            max_retries=LLM_MAX_RETRIES,
        )
    return _client


def chat(messages: list[dict], temperature: float = 0.3, max_tokens: int = 300) -> str:
    """调用模型并返回完整文本；超时/空响应/截断均明确失败。"""
    try:
        response = get_client().chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except (APITimeoutError, APIConnectionError) as exc:
        raise LLMError("模型服务连接或响应超时，请稍后重试") from exc
    except OpenAIError as exc:
        raise LLMError("模型服务调用失败，请检查模型配置或稍后重试") from exc

    if not response.choices:
        raise LLMOutputError("模型未返回有效内容")
    choice = response.choices[0]
    if getattr(choice, "finish_reason", None) == "length":
        raise LLMOutputError("模型输出因长度上限被截断，未作为完整结果使用")
    content = (choice.message.content or "").strip()
    if not content:
        raise LLMOutputError("模型返回了空内容")
    return content
