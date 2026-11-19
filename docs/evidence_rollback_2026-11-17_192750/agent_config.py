# -*- coding: utf-8 -*-
"""Agent 配置

凭据一律从环境变量读取，真实值放在 agent/.env（不在源码中出现，也不入库）。
首次部署请复制 agent/.env.example 为 agent/.env 并填写。

- 模型接口：DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL
- 智能问数数据库：DATAMIND_QUERY_USER / DATAMIND_QUERY_PASSWORD（只读账号）

注意：ETL 脚本使用各自的写入账号（etl/*/build_*.py），与此处配置无关。
"""

import os
from pathlib import Path

# 加载 agent/.env（若 python-dotenv 不可用则退化为手工解析，保证不因缺依赖而失效）
_ENV_FILE = Path(__file__).with_name(".env")


def _load_env_file(path: Path) -> None:
    """把 .env 里的键值加载进 os.environ（已存在的环境变量优先，不覆盖）"""
    if not path.exists():
        return
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv(path, override=False)
        return
    except ImportError:
        pass
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_env_file(_ENV_FILE)

# ==================== 大模型接口 ====================
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

BACKEND_URL = os.getenv("DATAMIND_BACKEND_URL", "http://localhost:8000")

# ==================== 智能问数数据库（只读账号） ====================
MYSQL_HOST = os.getenv("DATAMIND_MYSQL_HOST", "localhost")
MYSQL_PORT = int(os.getenv("DATAMIND_MYSQL_PORT", "3306"))
MYSQL_QUERY_USER = os.getenv("DATAMIND_QUERY_USER", "datamind_ro")
MYSQL_QUERY_PASSWORD = os.getenv("DATAMIND_QUERY_PASSWORD", "")

# 单次查询超时（毫秒），到点由 MySQL 终止，避免长查询拖垮服务
QUERY_TIMEOUT_MS = int(os.getenv("DATAMIND_QUERY_TIMEOUT_MS", "15000"))

# Agent 最多重试 SQL 次数
MAX_SQL_RETRIES = 2


def require_api_key() -> str:
    """取模型密钥，缺失时给出明确错误（不回显任何凭据）"""
    if not DEEPSEEK_API_KEY:
        raise RuntimeError(
            "未配置 DEEPSEEK_API_KEY：请在 agent/.env 或环境变量中设置后重启服务"
        )
    return DEEPSEEK_API_KEY
