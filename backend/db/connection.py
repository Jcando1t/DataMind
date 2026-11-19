# -*- coding: utf-8 -*-
"""MySQL 异步连接池模块

使用 SQLAlchemy + aiomysql 实现异步连接池，
提供 get_db() 异步依赖注入函数供各路由使用。
"""

import os
import sys
from pathlib import Path

from sqlalchemy import URL
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

# ==================== 数据库连接配置 ====================
# 指标查询只读，用专用只读账号（与 Agent 共用），凭据来自环境变量 / agent/.env，
# 不在源码中出现；写库仍由 ETL 脚本用自己的账号完成。
_ENV_FILE = Path(__file__).resolve().parents[2] / "agent" / ".env"


def _load_env_file(path: Path) -> None:
    """加载 .env 到环境变量（已存在的环境变量优先）"""
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

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))
from datamind_config import database_name, load_dataset_config

DATASET_CONFIG = load_dataset_config()

MYSQL_HOST = os.getenv("DATAMIND_MYSQL_HOST", "localhost")
MYSQL_PORT = int(os.getenv("DATAMIND_MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("DATAMIND_QUERY_USER", "datamind_ro")
MYSQL_PASSWORD = os.getenv("DATAMIND_QUERY_PASSWORD", "")

# 各库的连接地址（FastAPI 主要查 ads 库）
ADS_DATABASE = database_name("ads", DATASET_CONFIG)
DWS_DATABASE = database_name("dws", DATASET_CONFIG)

def build_mysql_url(
    database: str,
    *,
    username: str | None = None,
    password: str | None = None,
    host: str | None = None,
    port: int | None = None,
) -> URL:
    """结构化构建连接 URL，正确处理密码中的 @、:、#、/ 等特殊字符。"""
    return URL.create(
        "mysql+aiomysql",
        username=MYSQL_USER if username is None else username,
        password=MYSQL_PASSWORD if password is None else password,
        host=MYSQL_HOST if host is None else host,
        port=MYSQL_PORT if port is None else port,
        database=database,
    )


ADS_DB_URL = build_mysql_url(ADS_DATABASE)
DWS_DB_URL = build_mysql_url(DWS_DATABASE)

# 连接池大小配置
POOL_SIZE = 10          # 连接池常驻连接数
MAX_OVERFLOW = 20       # 超出连接池大小后最多允许的额外连接数
POOL_RECYCLE = 3600     # 连接回收时间（秒），避免 MySQL 8 小时断连问题
POOL_PRE_PING = True    # 取连接前先做 ping，保证连接可用

# ==================== 创建异步引擎（连接池） ====================
# ads 库引擎：指标查询路由使用
ads_engine = create_async_engine(
    ADS_DB_URL,
    echo=False,                 # 生产环境关闭 SQL 日志
    pool_size=POOL_SIZE,
    max_overflow=MAX_OVERFLOW,
    pool_recycle=POOL_RECYCLE,
    pool_pre_ping=POOL_PRE_PING,
)

# dws 库引擎：预留（Agent 查询用）
dws_engine = create_async_engine(
    DWS_DB_URL,
    echo=False,
    pool_size=POOL_SIZE,
    max_overflow=MAX_OVERFLOW,
    pool_recycle=POOL_RECYCLE,
    pool_pre_ping=POOL_PRE_PING,
)

# ==================== 会话工厂 ====================
# 每个请求从连接池获取一个会话，请求结束自动归还
AdsSessionLocal = async_sessionmaker(
    bind=ads_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

DwsSessionLocal = async_sessionmaker(
    bind=dws_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """SQLAlchemy 声明式基类（预留，供后续定义 ORM 模型）"""
    pass


# ==================== 依赖注入函数 ====================
async def get_db() -> AsyncSession:
    """FastAPI 异步依赖注入：提供 ads 库的数据库会话

    使用方式（路由函数参数）：
        db: AsyncSession = Depends(get_db)

    请求结束后自动关闭会话，连接归还连接池。
    """
    async with AdsSessionLocal() as session:
        yield session


async def get_dws_db() -> AsyncSession:
    """FastAPI 异步依赖注入：提供 dws 库的数据库会话（Agent 查询用）"""
    async with DwsSessionLocal() as session:
        yield session
