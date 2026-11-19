# -*- coding: utf-8 -*-
"""指标查询路由（给前端看板用）

查询当前配置的 ADS 数据库中四张标准 ADS 表：
- GET /api/metrics/overview        GMV 总览
- GET /api/metrics/trend           GMV 月度趋势（按 year_month 升序）
- GET /api/metrics/category-rank   品类排行（按 rank 升序）
- GET /api/metrics/top-sellers      卖家排行 Top20（按 rank 升序）
"""

from typing import Any, Dict, List

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from db.connection import DATASET_CONFIG, get_db
from models.schemas import success_response

router = APIRouter()


@router.get("/config")
async def get_dataset_config():
    config = DATASET_CONFIG
    return success_response({key: config[key] for key in (
        "display_name", "region", "currency", "currency_symbol", "currency_locale", "currency_label",
        "timezone", "gmv_definition", "database_prefix",
    )})


async def _query(db: AsyncSession, sql: str) -> List[Dict[str, Any]]:
    """执行查询并把结果行转成字典列表（金额类字段强制转 float）"""
    result = await db.execute(text(sql))
    rows = result.mappings().all()  # 每行是 RowMapping（类字典）
    data = []
    for row in rows:
        item = {}
        for key, value in row.items():
            # Decimal 一律转 float，避免序列化问题
            if hasattr(value, "quantize"):  # Decimal 的特征方法
                value = float(value)
            item[key] = value
        data.append(item)
    return data


@router.get("/overview")
async def get_overview(db: AsyncSession = Depends(get_db)):
    """GMV 总览：返回 ads_gmv_overview 全部字段（单行表）"""
    sql = """
        SELECT total_gmv, total_orders, avg_order_value,
               total_customers, data_period_start, data_period_end
        FROM ads_gmv_overview
        LIMIT 1
    """
    rows = await _query(db, sql)
    return success_response(data=rows[0] if rows else None)


@router.get("/trend")
async def get_trend(db: AsyncSession = Depends(get_db)):
    """GMV 月度趋势：返回 ads_gmv_trend 全部数据，按 year_month 升序"""
    sql = """
        SELECT `year_month`, monthly_gmv, monthly_orders, mom_growth_rate
        FROM ads_gmv_trend
        ORDER BY `year_month` ASC
    """
    rows = await _query(db, sql)
    return success_response(data=rows)


@router.get("/category-rank")
async def get_category_rank(db: AsyncSession = Depends(get_db)):
    """品类排行：返回 ads_category_rank 全部数据，按 rank 升序"""
    sql = """
        SELECT `rank`, category_english, total_gmv, total_orders, gmv_ratio
        FROM ads_category_rank
        ORDER BY `rank` ASC
    """
    rows = await _query(db, sql)
    return success_response(data=rows)


@router.get("/top-sellers")
async def get_top_sellers(db: AsyncSession = Depends(get_db)):
    """卖家排行 Top20：返回 ads_top_sellers 全部数据，按 rank 升序"""
    sql = """
        SELECT `rank`, seller_id, seller_city, seller_state, total_gmv, total_orders
        FROM ads_top_sellers
        ORDER BY `rank` ASC
    """
    rows = await _query(db, sql)
    return success_response(data=rows)
