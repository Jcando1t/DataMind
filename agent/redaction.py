# -*- coding: utf-8 -*-
"""响应与日志的统一凭据脱敏；只读取环境变量，不记录任何真实值。"""
from __future__ import annotations

import os
from typing import Any

# 需要从环境变量读取的凭据键。注意：任何非空值都必须被替换，
# 不设置最小长度门槛——短密码同样是配置秘密，不得因长度不足被忽略。
_SECRET_ENV_NAMES = (
    "DEEPSEEK_API_KEY",
    "DATAMIND_QUERY_PASSWORD",
    # 兼容历史变量名，避免旧部署配置残留导致漏脱敏
    "MYSQL_QUERY_PASSWORD",
)


def _configured_secrets() -> list[str]:
    """收集当前进程配置中的非空秘密，长值优先替换，避免短值先替换造成残留。"""
    names = set(_SECRET_ENV_NAMES) | {"DATAMIND_INGEST_TOKEN"}
    names.update(name for name in os.environ if name.startswith("DATAMIND_SOURCE_") and name.endswith("_PASSWORD"))
    values = {os.getenv(name, "") for name in names}
    # 同时兼容 config 模块已加载但环境变量被后续改动的场景：
    # config 从 .env 加载时会同步写入 os.environ，因此此处读取环境变量即可覆盖。
    return sorted((s for s in values if s), key=len, reverse=True)


def sanitize_text(value: Any) -> str:
    """替换当前配置中的全部凭据；输入统一转为字符串。"""
    text = "" if value is None else str(value)
    for secret in _configured_secrets():
        text = text.replace(secret, "***")
    return text


def sanitize_data(value: Any) -> Any:
    """递归脱敏完整响应，包括 SQL、rows、图表配置和嵌套错误正文。"""
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            base = sanitize_text(key)
            label, index = base, 2
            # 不同原键脱敏后可能相同，保留所有值。
            while label in cleaned:
                label = f"{base}#{index}"
                index += 1
            cleaned[label] = sanitize_data(item)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [sanitize_data(item) for item in value]
    if isinstance(value, str):
        return sanitize_text(value)
    return value
