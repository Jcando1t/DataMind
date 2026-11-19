# -*- coding: utf-8 -*-
"""数据字典检索路由（给 Agent / LLM 用）

- GET /api/schema/list             所有表的名称和描述（不含字段详情）
- GET /api/schema/search?q=关键词   关键词匹配搜索，返回相关表（含完整字段列表）
- GET /api/schema/{table_name}      指定表的完整数据字典（含字段列表）

匹配逻辑：在 table_name、table_desc、字段 name 和字段 desc 中做字符串包含匹配（不区分大小写）
"""

from typing import List

from fastapi import APIRouter, HTTPException, Query

from data_dictionary import DATA_DICTIONARY
from models.schemas import success_response

router = APIRouter()


@router.get("/list")
async def list_tables():
    """返回所有表的名称和描述（不含字段详情）"""
    data = [
        {"table_name": t["table_name"], "table_desc": t["table_desc"]}
        for t in DATA_DICTIONARY
    ]
    return success_response(data=data)


def _match_table(table: dict, keyword: str) -> bool:
    """判断某张表是否命中关键词

    匹配范围：表名、表描述、字段名、字段描述（统一转小写做包含匹配）
    """
    kw = keyword.lower()
    # 表名 / 表描述
    if kw in table["table_name"].lower() or kw in table["table_desc"].lower():
        return True
    # 字段名 / 字段描述
    for field in table["fields"]:
        if kw in field["name"].lower() or kw in field["desc"].lower():
            return True
    return False


@router.get("/search")
async def search_schema(q: str = Query(..., description="搜索关键词")):
    """关键词搜索数据字典，返回匹配到的表信息（含完整字段列表）"""
    if not q.strip():
        return success_response(data=[])

    matched: List[dict] = [
        table for table in DATA_DICTIONARY if _match_table(table, q.strip())
    ]
    return success_response(data=matched)


@router.get("/{table_name}")
async def get_table_schema(table_name: str):
    """返回指定表的完整数据字典（含字段列表）"""
    for table in DATA_DICTIONARY:
        if table["table_name"] == table_name:
            return success_response(data=table)
    # 未找到时抛 404，由全局异常处理器包装成统一错误格式
    raise HTTPException(status_code=404, detail=f"数据字典中不存在表：{table_name}")
