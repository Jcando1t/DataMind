# -*- coding: utf-8 -*-
"""SQL 只读结构校验（执行层硬限制，不依赖提示词）

设计要点：
1. 用 sqlglot 按 MySQL 方言把 SQL 解析成语法树，而不是做字符串包含匹配——
   换行/反引号/注释/字符串字面量都无法绕过语法树的结构判断。
2. 只允许单条查询语句（SELECT / UNION / INTERSECT / EXCEPT），
   解析失败一律拒绝（fail closed）。
3. 结构级禁止：SELECT ... INTO（文件或变量）、系统库、非授权表、
   加锁读（FOR UPDATE / LOCK IN SHARE MODE）、系统变量、危险函数。
4. 表名白名单以 backend/data_dictionary.py 为唯一来源，与数据库授权保持一致。
5. 结果行数上限只作用于**最外层**查询，子查询内的 LIMIT 不受影响，
   并保留 LIMIT ... OFFSET 语义。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import Scope, traverse_scope

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datamind_config import database_name, load_dataset_config

_CONFIG = load_dataset_config()
_ADS_DATABASE = database_name("ads", _CONFIG)
_DWS_DATABASE = database_name("dws", _CONFIG)

# 单次返回的最大行数（最外层 LIMIT）
MAX_ROWS = 1000

# 兜底白名单：正常情况下从 backend/data_dictionary.py 载入，二者必须一致
_FALLBACK_ALLOWED = {
    "ads_gmv_overview": _ADS_DATABASE,
    "ads_gmv_trend": _ADS_DATABASE,
    "ads_category_rank": _ADS_DATABASE,
    "ads_top_sellers": _ADS_DATABASE,
    "dws_daily_trade": _DWS_DATABASE,
    "dws_daily_active_users": _DWS_DATABASE,
    "dws_category_summary": _DWS_DATABASE,
}

# 明确禁止的函数（文件读写、拖时间、锁等待等）
FORBIDDEN_FUNCTIONS = {
    "load_file",
    "sleep",
    "benchmark",
    "get_lock",
    "release_lock",
    "master_pos_wait",
    "sys_eval",
    "sys_exec",
}

# 允许出现在最外层的查询类型
_QUERY_TYPES = (exp.Select, exp.Union, exp.Intersect, exp.Except)


class SqlGuardError(Exception):
    """SQL 被结构校验拒绝"""

    def __init__(self, message: str, category: str = "guard"):
        super().__init__(message)
        self.message = message
        self.category = category


def _load_allowed_tables() -> dict[str, str]:
    """从数据字典载入 {表名: 库名} 白名单；不可用时退回内置常量"""
    try:
        from backend.data_dictionary import DATA_DICTIONARY  # 延迟导入，避免循环依赖

        tables = {
            str(item["table_name"]).lower(): str(item["database"]).lower()
            for item in DATA_DICTIONARY
        }
        if tables:
            return tables
    except Exception:  # noqa: BLE001 - 数据字典不可用时用内置白名单兜底
        pass
    return dict(_FALLBACK_ALLOWED)


# 授权表 → 库（与数据库 GRANT 保持一致）
ALLOWED_TABLES = _load_allowed_tables()
# 授权库集合（系统库 mysql/information_schema/sys 天然不在其中）
ALLOWED_DATABASES = set(ALLOWED_TABLES.values())


def parse_single(sql: str) -> exp.Expression:
    """解析成语法树，要求恰好一条语句"""
    text = (sql or "").strip()
    if not text:
        raise SqlGuardError("SQL 为空")
    try:
        statements = [s for s in sqlglot.parse(text, read="mysql") if s is not None]
    except Exception as exc:  # sqlglot 的 ParseError 等
        raise SqlGuardError(f"SQL 无法解析，已拒绝执行：{str(exc).splitlines()[0]}") from exc
    if not statements:
        raise SqlGuardError("SQL 为空或只包含注释")
    if len(statements) > 1:
        raise SqlGuardError("安全限制：禁止多语句执行（解析到多条语句）")
    return statements[0]


def check_structure(stmt: exp.Expression) -> None:
    """结构级检查：语句类型、INTO、加锁、系统变量、危险函数"""
    if not isinstance(stmt, _QUERY_TYPES):
        raise SqlGuardError(
            f"安全限制：只允许只读查询，当前语句类型为 {type(stmt).__name__}"
        )

    # SELECT ... INTO OUTFILE / DUMPFILE / @var 都会产生 Into 节点
    if any(isinstance(node, exp.Into) for node in stmt.walk()):
        raise SqlGuardError("安全限制：禁止 SELECT ... INTO（文件写出或变量赋值）")

    # FOR UPDATE / LOCK IN SHARE MODE：遍历整棵树，覆盖子查询、CTE 和 UNION 分支
    if any(node.args.get("locks") for node in stmt.walk()):
        raise SqlGuardError("安全限制：禁止加锁读（FOR UPDATE / LOCK IN SHARE MODE）")

    # 系统变量（@@datadir 等）
    for node in stmt.walk():
        if isinstance(node, exp.SessionParameter):
            raise SqlGuardError("安全限制：禁止访问数据库系统变量")

    # 危险函数
    for node in stmt.find_all(exp.Anonymous):
        name = (node.name or "").lower()
        if name in FORBIDDEN_FUNCTIONS:
            raise SqlGuardError(f"安全限制：禁止调用函数 {name}()")
    for node in stmt.find_all(exp.Func):
        name = (node.sql_name() or "").lower()
        if name in FORBIDDEN_FUNCTIONS:
            raise SqlGuardError(f"安全限制：禁止调用函数 {name}()")


def resolve_tables(
    stmt: exp.Expression, table_db: dict[str, str] | None = None
) -> list[str]:
    """校验并补全表引用，返回被引用的 `库.表` 列表

    - CTE 名称不是真实表引用，跳过
    - 无库名前缀时，用 table_db（来自 Schema 上下文）或白名单推断
    - 系统库、非授权表一律拒绝
    """
    table_db = table_db or {}
    resolved: list[str] = []
    try:
        scopes = list(traverse_scope(stmt))
    except Exception as exc:
        raise SqlGuardError("SQL 查询作用域无法解析，已拒绝执行") from exc

    # 按作用域区分 CTE 引用与物理表。显式 db.table 永远按物理表校验，
    # 即使它与当前/外层 CTE 同名也不能跳过；嵌套 CTE 由 traverse_scope 逐层展开。
    for scope in scopes:
        for table in scope.tables:
            source = scope.sources.get(table.alias_or_name)
            if not table.db and isinstance(source, Scope):
                continue  # 无库名且解析到子作用域，才是真实 CTE 引用

            name = (table.name or "").strip()
            if not name:
                continue
            lowered = name.lower()
            if table.catalog:
                raise SqlGuardError(f"安全限制：禁止跨实例引用（{table.catalog}.{name}）")

            db = (table.db or "").strip().lower()
            if not db:
                db = (table_db.get(lowered) or ALLOWED_TABLES.get(lowered) or "").lower()
            if not db:
                raise SqlGuardError(f"安全限制：无法确定表 {name} 所属库，或该表不在授权范围内")
            if db not in ALLOWED_DATABASES:
                raise SqlGuardError(f"安全限制：禁止访问库 {db}（仅允许 ADS/DWS 授权表）")
            if ALLOWED_TABLES.get(lowered) != db:
                raise SqlGuardError(f"安全限制：表 {db}.{name} 不在授权范围内")

            table.set("db", exp.to_identifier(db))
            table.set("catalog", None)
            ref = f"{db}.{lowered}"
            if ref not in resolved:
                resolved.append(ref)
    return resolved


def apply_limit(stmt: exp.Expression, max_rows: int = MAX_ROWS) -> str:
    """把最外层结果行数限制在 max_rows 内

    返回：added=原本无 LIMIT 已追加 / capped=原 LIMIT 过大已收紧 / kept=原 LIMIT 合规
    子查询内层 LIMIT 不受影响（只处理最外层节点）。
    """
    limit = stmt.args.get("limit")
    if limit is None:
        stmt.limit(max_rows, copy=False)
        return "added"

    expr = limit.expression
    if not isinstance(expr, exp.Literal) or not expr.is_int:
        raise SqlGuardError("安全限制：LIMIT 必须是整数字面量")
    if int(expr.this) > max_rows:
        limit.set("expression", exp.Literal.number(max_rows))
        return "capped"
    return "kept"


def render(stmt: exp.Expression) -> str:
    """生成可执行的 MySQL SQL（标识符加反引号，不含结尾分号）"""
    return stmt.sql(dialect="mysql", identify=True)


def prepare_readonly_query(
    sql: str,
    table_db: dict[str, str] | None = None,
    max_rows: int = MAX_ROWS,
) -> tuple[str, dict]:
    """校验 + 补全 + 限行，返回 (可执行 SQL, 元信息)

    元信息：{"tables": [...], "limit_action": added|capped|kept}
    任何违规抛 SqlGuardError。
    """
    stmt = parse_single(sql)
    check_structure(stmt)
    tables = resolve_tables(stmt, table_db)
    action = apply_limit(stmt, max_rows)
    return render(stmt), {"tables": tables, "limit_action": action}


def extract_table_db(schema_context: str) -> dict[str, str]:
    """从 Schema 上下文中解析 表名 → 库名 映射"""
    return {
        name.lower(): db.lower()
        for name, db in re.findall(
            r"表名:\s*(\w+)\s*\(数据库:\s*(\w+)\)", schema_context or ""
        )
    }
