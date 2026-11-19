# -*- coding: utf-8 -*-
"""响应与日志的统一凭据脱敏；只读取环境变量，不记录任何真实值。"""
from __future__ import annotations

import os
from typing import Any

_SECRET_ENV_NAMES = ("DEEPSEEK_API_KEY", "DATAMIND_QUERY_PASSWORD")


def sanitize_text(value: Any) -> str:
    """替换当前配置中的凭据；输入统一转为字符串。"""
    text = "" if value is None else str(value)
    secrets = {
        os.getenv(name, "")
        for name in _SECRET_ENV_NAMES
    }
    for secret in sorted((s for s in secrets if len(s) >= 6), key=len, reverse=True):
        text = text.replace(secret, "***")
    return text


def sanitize_data(value: Any) -> Any:
    """递归脱敏完整响应，包括 SQL、rows、图表配置和嵌套错误正文。"""
    if isinstance(value, dict):
        return {str(key): sanitize_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_data(item) for item in value]
    if isinstance(value, str):
        return sanitize_text(value)
    return value
