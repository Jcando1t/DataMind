# -*- coding: utf-8 -*-
"""节点4：SQL 执行与校验
执行生成的 SQL，捕获错误供重试节点判断。

只读边界（执行层硬限制，不依赖提示词）：
- 数据库账号是专用只读账号（仅 ADS/DWS 授权表 SELECT），见 agent/config.py
- SQL 经 agent/sql_guard.py 按语法树校验：单条查询、禁 INTO/系统库/非授权表/加锁读
- 连接后设置并回读只读会话与查询超时，任一步失败都中止查询

结果规模：
- 只对最外层加/收紧 LIMIT（子查询不受影响），默认 1000 行
- 用流式游标 + fetchmany 有界提取，避免把大结果集全部加载进客户端内存

错误分类（供图与 API 判定，前端可区分）：
- connection：连接/认证失败，重试无意义
- guard：被只读策略拒绝，可通过重新生成 SQL 修复
- configuration：保护配置不完整，不重试；unsupported：现有表无法回答，不重试
- sql：SQL 语法/执行错误，可重试
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from datetime import date
import pymysql

from agent.config import (
    MYSQL_HOST,
    MYSQL_PORT,
    require_query_password,
    MYSQL_QUERY_USER,
    QUERY_TIMEOUT_MS,
)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from datamind_config import database_name, load_dataset_config

DATASET_CONFIG = load_dataset_config()
from agent.sql_guard import (
    MAX_ROWS,
    SqlGuardError,
    apply_limit,
    extract_table_db,
    parse_single,
    prepare_readonly_query,
    render,
    resolve_tables,
)
from agent.state import AgentState

# 数据库连接失败 / 认证失败的 MySQL 错误码（重试无意义）
CONNECTION_ERROR_CODES = {2002, 2003, 2005, 1040, 1044, 1045, 1081, 1130, 1698, 2995}
logger = logging.getLogger("datamind.agent")


class SessionConfigurationError(RuntimeError):
    """会话保护设置或回读失败，禁止继续执行。"""


def _configure_and_verify_session(cursor) -> None:
    try:
        cursor.execute("SET SESSION TRANSACTION READ ONLY")
        cursor.execute("SET SESSION MAX_EXECUTION_TIME = %s", (int(QUERY_TIMEOUT_MS),))
        cursor.execute("SELECT @@SESSION.transaction_read_only AS read_only, @@SESSION.max_execution_time AS max_execution_time")
        status = cursor.fetchone()
        while cursor.fetchone() is not None:
            pass
    except pymysql.MySQLError as exc:
        raise SessionConfigurationError("数据库只读会话或查询超时配置失败") from exc
    if not status or int(status.get("read_only", 0)) != 1 or int(status.get("max_execution_time", 0)) != int(QUERY_TIMEOUT_MS):
        raise SessionConfigurationError("数据库会话保护回读校验未通过，本次查询已中止")


def _read_data_period(cursor) -> dict:
    """同一只读会话读取真实日期；元信息不可用时不推断月份完整性。"""
    try:
        ads = database_name("ads", DATASET_CONFIG)
        cursor.execute(f"SELECT data_period_start, data_period_end, "
                       f"(SELECT COUNT(DISTINCT category_english) FROM `{ads}`.ads_category_rank) AS category_count "
                       f"FROM `{ads}`.ads_gmv_overview LIMIT 1")
        rows = cursor.fetchmany(2)
        if len(rows) != 1:
            return {}
        period = {key: date.fromisoformat(str(rows[0][key])[:10]).isoformat()
                  for key in ("data_period_start", "data_period_end")}
        if isinstance(rows[0].get("category_count"), int) and rows[0]["category_count"] > 0:
            period["category_count"] = rows[0]["category_count"]
        return period if period["data_period_start"] <= period["data_period_end"] else {}
    except (pymysql.MySQLError, KeyError, TypeError, ValueError):
        logger.warning("数据覆盖元信息不可用，本次不判断月份完整性")
        return {}


# ==================== 兼容旧测试的辅助函数 ====================
def _build_table_db_map(schema_context: str) -> dict[str, str]:
    """从 Schema 上下文解析 表名 → 库名 映射"""
    return extract_table_db(schema_context)


def _validate_sql(sql: str, table_db: dict[str, str] | None = None) -> tuple[str, str | None]:
    """结构校验入口：返回 (可执行 SQL, 错误信息)"""
    try:
        prepared, _ = prepare_readonly_query(sql, table_db)
        return prepared, None
    except SqlGuardError as exc:
        return (sql or "").strip(), exc.message


def _enforce_limit(sql: str, max_rows: int = MAX_ROWS) -> str:
    """只做行数上限（不做表名校验），返回带分号的 SQL"""
    try:
        stmt = parse_single(sql)
        apply_limit(stmt, max_rows)
        return render(stmt) + ";"
    except SqlGuardError:
        return (sql or "").strip().rstrip(";") + ";"


def _qualify_tables(sql: str, table_db: dict[str, str]) -> str:
    """为裸表名补全库名前缀（基于语法树，不会改动字符串字面量）"""
    try:
        stmt = parse_single(sql)
        resolve_tables(stmt, table_db)
        return render(stmt)
    except SqlGuardError:
        return sql


# ==================== 执行节点 ====================
def _error(state: AgentState, message: str, category: str) -> AgentState:
    return {
        **state,
        "sql_error": message,
        "sql_error_type": category,
        "sql_result": None,
        "result_truncated": False,
        "sql_meta": None,
    }


def sql_execution_node(state: AgentState) -> AgentState:
    """执行 SQL，成功则写入 sql_result，失败则写入 sql_error + sql_error_type"""
    sql = state.get("sql", "")
    if state.get("sql_error_type") == "unsupported":
        return _error(state, state.get("sql_error") or "现有汇总表不支持该指标", "unsupported")
    if not sql:
        return _error(state, "SQL 为空", "sql")

    # 1) 结构校验 + 表名白名单 + 最外层限行
    try:
        prepared, meta = prepare_readonly_query(
            sql, _build_table_db_map(state.get("schema_context", ""))
        )
    except SqlGuardError as exc:
        return _error(state, exc.message, exc.category)

    # 2) 执行（只读账号 + 只读会话 + 有界流式提取）
    try:
        query_password = require_query_password()
        # 不指定 database：SQL 里的表名都已是全限定 `库`.`表`
        conn = pymysql.connect(
            host=MYSQL_HOST,
            port=MYSQL_PORT,
            user=MYSQL_QUERY_USER,
            password=query_password,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.SSDictCursor,  # 流式游标，不预读全部结果
            connect_timeout=10,
            read_timeout=max(15, QUERY_TIMEOUT_MS // 1000 + 5),
            autocommit=True,
        )
        with conn:
            with conn.cursor() as cursor:
                _configure_and_verify_session(cursor)
                cursor.execute(prepared)
                # 有界提取：多取一行用于判断是否被截断，绝不 fetchall
                raw_rows = cursor.fetchmany(MAX_ROWS + 1)
                # LIMIT 已由校验层写在 SQL 里，客户端拿不到"还有更多行"的信号，
                # 因此当上限由我们追加/收紧、且结果恰好顶到上限时，保守标记为已截断
                truncated = len(raw_rows) > MAX_ROWS or (
                    meta.get("limit_action") in {"added", "capped"}
                    and len(raw_rows) >= MAX_ROWS
                )
                raw_rows = raw_rows[:MAX_ROWS]
                data_period = _read_data_period(cursor)

        # Decimal → float，datetime → 字符串，便于 JSON 序列化
        result = []
        for row in raw_rows:
            clean = {}
            for k, v in row.items():
                if hasattr(v, "quantize"):
                    v = float(v)
                elif hasattr(v, "strftime"):
                    v = str(v)
                clean[k] = v
            result.append(clean)

        return {
            **state,
            "sql": prepared,
            "sql_result": result,
            "sql_error": None,
            "sql_error_type": None,
            "result_truncated": truncated,
            "sql_meta": {**meta, "session_read_only_verified": True,
                         "query_timeout_ms": int(QUERY_TIMEOUT_MS), "data_period": data_period},
        }

    except (SessionConfigurationError, RuntimeError) as e:
        return _error(state, str(e), "configuration")
    except pymysql.err.OperationalError as e:
        code = e.args[0] if e.args else None
        message = e.args[1] if len(e.args) > 1 else str(e)
        if code in CONNECTION_ERROR_CODES:
            # 数据库连接/认证失败：重试没有意义，明确标注类型
            return _error(state, f"数据库连接失败（错误码 {code}）：{message}", "connection")
        return _error(state, f"MySQL 执行错误（错误码 {code}）：{message}", "sql")
    except pymysql.MySQLError as e:
        code = e.args[0] if e.args else None
        message = e.args[1] if len(e.args) > 1 else str(e)
        return _error(state, f"MySQL 执行错误（错误码 {code}）：{message}", "sql")
    except Exception as e:  # noqa: BLE001 - 兜底，避免节点异常打断整张图
        return _error(state, f"SQL 执行异常：{e}", "sql")
