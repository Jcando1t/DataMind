# -*- coding: utf-8 -*-
"""Pydantic 响应模型定义

约定：
1. 所有金额/数量类数字字段统一用 float，避免 Decimal 序列化问题
2. 提供统一响应包装函数，保证所有接口返回 {"code", "message", "data"} 格式
"""

from typing import Any, List, Optional

from pydantic import BaseModel


# ==================== 统一响应包装 ====================
class ApiResponse(BaseModel):
    """统一响应格式"""
    code: int = 200
    message: str = "success"
    data: Optional[Any] = None


def success_response(data: Any = None) -> dict:
    """构造成功响应

    Args:
        data: 业务数据

    Returns:
        {"code": 200, "message": "success", "data": ...}
    """
    return {"code": 200, "message": "success", "data": data}


# ==================== 指标模型（标准 ADS 表结构） ====================
class GmvOverview(BaseModel):
    """GMV 总览（ads_gmv_overview，单行）"""
    total_gmv: float                    # 总成交金额
    total_orders: int                   # 总订单数
    avg_order_value: float              # 平均客单价
    total_customers: int                # 总用户数
    data_period_start: Optional[str] = None   # 数据起始日期
    data_period_end: Optional[str] = None     # 数据截止日期


class GmvTrendItem(BaseModel):
    """GMV 月度趋势（ads_gmv_trend，每行一个月）"""
    year_month: str                     # 月份，如 2024-01
    monthly_gmv: float                  # 当月成交金额
    monthly_orders: int                 # 当月订单数
    mom_growth_rate: Optional[float] = None  # 环比增长率


class CategoryRankItem(BaseModel):
    """品类排行（ads_category_rank）"""
    rank: int                           # 排名
    category_english: str               # 品类英文名
    total_gmv: float                    # 品类总成交金额
    total_orders: int                   # 品类总订单数
    gmv_ratio: Optional[float] = None    # GMV 占比


class TopSellerItem(BaseModel):
    """卖家排行（ads_top_sellers，Top20）"""
    rank: int                           # 排名
    seller_id: str                      # 卖家ID
    seller_city: Optional[str] = None   # 卖家所在城市
    seller_state: Optional[str] = None  # 卖家所在州
    total_gmv: float                    # 卖家总成交金额
    total_orders: int                   # 卖家总订单数


# ==================== 数据字典模型（/api/schema 用） ====================
class FieldInfo(BaseModel):
    """字段信息"""
    name: str                           # 字段名
    desc: str                           # 字段中文描述


class TableSchema(BaseModel):
    """表信息（含完整字段列表）"""
    table_name: str                     # 表名
    table_desc: str                     # 表中文描述
    database: str                       # 所属数据库
    fields: List[FieldInfo]             # 字段列表


class TableBrief(BaseModel):
    """表摘要（不含字段详情）"""
    table_name: str                     # 表名
    table_desc: str                     # 表中文描述
