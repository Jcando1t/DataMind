# -*- coding: utf-8 -*-
"""查询结果的最小语义：按字段来源识别单位，不按输出别名猜测。"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import Scope, build_scope

from backend.data_dictionary import DATA_DICTIONARY
from datamind_config import load_dataset_config

_CURRENCY = load_dataset_config()["currency"]

# kind、单位、显示名称；百分比与均值均不可逐行累加。
FIELDS = {
    "gmv": ("money", _CURRENCY, "GMV"),
    "total_gmv": ("money", _CURRENCY, "GMV"),
    "monthly_gmv": ("money", _CURRENCY, "GMV"),
    "avg_order_value": ("average", _CURRENCY, "客单价"),
    "avg_price": ("average", _CURRENCY, "商品均价"),
    "total_orders": ("orders", "orders", "订单数"),
    "monthly_orders": ("orders", "orders", "订单数"),
    "order_count": ("orders", "orders", "订单数"),
    "total_customers": ("users", "users", "去重下单客户数"),
    "active_user_count": ("daily_users", "users", "当日去重下单用户数"),
    "seller_count": ("count", "count", "卖家数"),
    "gmv_ratio": ("percent", "percent", "GMV占比"),
    "mom_growth_rate": ("percent", "percent", "GMV环比"),
    "year_month": ("month", "", "月份"),
    "order_date": ("date", "", "日期"),
    "data_period_start": ("date", "", "数据起始日期"),
    "data_period_end": ("date", "", "数据截止日期"),
    "rank": ("rank", "", "排名"),
    "category_english": ("dimension", "", "品类"),
    "seller_id": ("dimension", "", "卖家"),
    "seller_city": ("dimension", "", "卖家城市"),
    "seller_state": ("dimension", "", "卖家州"),
}
TABLE_FIELDS = {t["table_name"]: {f["name"] for f in t["fields"]} for t in DATA_DICTIONARY}


def number(value) -> Decimal | None:
    """NULL、布尔、格式错误和非有限数不是可核验的数字。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result.adjusted() < 24 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def field_spec(name: str, table: str = "") -> dict:
    kind, unit, label = FIELDS.get(name.lower(), ("unknown", "", name))
    return {"kind": kind, "unit": unit, "label": label, "source_column": name.lower(), "source_table": table}


def output_specs(sql: str | None, columns: list[str]) -> dict[str, dict]:
    """仅追溯能确认的列、ROUND/CAST 和聚合；复杂表达式回退为未知单位。"""
    if not sql:
        return {c: field_spec(c) for c in columns}
    unknown = {c: field_spec(c, "") | {"kind": "unknown", "unit": "", "label": c} for c in columns}
    try:
        scope = build_scope(sqlglot.parse_one(sql, read="mysql"))
        if not scope or not isinstance(scope.expression, exp.Select):
            return unknown

        def source_spec(source, name, depth):
            if isinstance(source, exp.Table):
                table = source.name.lower()
                if name.lower() in TABLE_FIELDS.get(table, set()):
                    return field_spec(name, table)
            elif isinstance(source, Scope) and depth < 8:
                for projection in source.expression.selects:
                    if projection.alias_or_name.lower() == name.lower():
                        return infer(projection, source, depth + 1)
                    if isinstance(projection, exp.Star):
                        return infer(exp.column(name), source, depth + 1)
            return None

        def infer(node, current, depth=0):
            if depth > 8:
                return None
            if isinstance(node, (exp.Alias, exp.Paren, exp.Round, exp.Cast)):
                return infer(node.this, current, depth + 1)
            if isinstance(node, exp.Column):
                if node.table:
                    return source_spec(current.sources.get(node.table), node.name, depth + 1)
                found = [s for src in current.sources.values() if (s := source_spec(src, node.name, depth + 1))]
                return found[0] if len(found) == 1 else None
            if isinstance(node, (exp.Sum, exp.Avg, exp.Min, exp.Max)):
                spec = infer(node.this, current, depth + 1)
                if spec:
                    spec = dict(spec)
                    if isinstance(node, exp.Sum):
                        # 百分比、均值与去重客户数的 SUM 不继承其原有业务含义。
                        if spec["kind"] not in {"money", "orders"}:
                            return None
                    elif isinstance(node, exp.Avg):
                        if not spec["unit"]:
                            return None
                        spec.update(kind="average", label="平均" + spec["label"])
                    elif isinstance(node, (exp.Min, exp.Max)):
                        spec["label"] = ("最小" if isinstance(node, exp.Min) else "最大") + spec["label"]
                    return spec
            if isinstance(node, exp.Count):
                return {"kind": "count", "unit": "count", "label": "记录数", "source_column": "", "source_table": ""}
            if isinstance(node, exp.TimeToStr):
                fmt = node.args.get("format")
                spec = infer(node.this, current, depth + 1)
                if spec and spec["kind"] == "date" and isinstance(fmt, exp.Literal) and fmt.this == "%Y-%m":
                    return spec | {"kind": "month", "label": "月份"}
            return None

        specs = {}
        for projection in scope.expression.selects:
            if isinstance(projection, exp.Star) or isinstance(projection, exp.Column) and projection.is_star:
                for col in columns:
                    node = exp.column(col, table=projection.table if isinstance(projection, exp.Column) else None)
                    if spec := infer(node, scope):
                        specs[col] = spec
            elif projection.alias_or_name in columns:
                if spec := infer(projection, scope):
                    specs[projection.alias_or_name] = spec
        return unknown | specs
    except (sqlglot.errors.SqlglotError, ValueError, TypeError, AttributeError):
        return unknown


def time_grain(question: str) -> str:
    """只用于保留日粒度；不能把“某日以来”的期间去重当作单日。"""
    import re
    q = question.lower()
    if any(w in q for w in ("每日", "每天", "按日", "按天", "逐日", "daily", "each day", "per day")):
        return "daily"
    dates = re.findall(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{4}年\d{1,2}月\d{1,2}[日号]", q)
    is_range = any(w in q for w in ("以来", "之前", "之后", "至今", "到现在", "期间", "区间", "之间")) or bool(re.search(r"至|到|~|～", q))
    if len(dates) == 1 and not is_range:
        return "day"
    return "period"
