# -*- coding: utf-8 -*-
"""受保护的 MySQL 只读接入；快照隔离保存，不触发现有覆盖式 ETL。"""
import asyncio
import csv
import hmac
import json
import os
import re
import ssl
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import aiomysql
from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, model_validator

ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = ROOT / "backend" / "sources.local.json"
STAGING = ROOT / "data" / "ingestion"
IDENTIFIER = r"^[A-Za-z_][A-Za-z0-9_]{0,63}$"
SOURCE_ID = r"^[a-z][a-z0-9_]{0,31}$"
MAX_BYTES = 200 * 1024 * 1024
MAX_CELL_BYTES = 1024 * 1024
TIMEOUT_SECONDS = 120
_slots = asyncio.Semaphore(2)
_active_jobs = set()
_bearer = HTTPBearer(auto_error=False)

# 只校验现有 DWD 实际消费的必要字段；其余原始字段可自行映射。
ENTITIES = {
    "orders": ("olist_orders_dataset.csv", {"order_id", "customer_id", "order_status", "order_purchase_timestamp"}),
    "order_items": ("olist_order_items_dataset.csv", {"order_id", "order_item_id", "product_id", "seller_id", "price", "freight_value"}),
    "customers": ("olist_customers_dataset.csv", {"customer_id", "customer_unique_id", "customer_city", "customer_state"}),
    "products": ("olist_products_dataset.csv", {"product_id", "product_category_name", "product_weight_g"}),
    "sellers": ("olist_sellers_dataset.csv", {"seller_id", "seller_city", "seller_state"}),
    "order_payments": ("olist_order_payments_dataset.csv", {"order_id", "payment_sequential", "payment_type", "payment_installments", "payment_value"}),
    "order_reviews": ("olist_order_reviews_dataset.csv", {"review_id", "order_id", "review_score"}),
    "product_category_name_translation": ("product_category_name_translation.csv", {"product_category_name", "product_category_name_english"}),
}
Identifier = Annotated[str, Field(pattern=IDENTIFIER)]


def require_operator(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)):
    token = os.getenv("DATAMIND_INGEST_TOKEN", "")
    if len(token) < 32:
        raise HTTPException(503, "数据接入未启用：请配置至少 32 位 DATAMIND_INGEST_TOKEN")
    provided = credentials.credentials if credentials and credentials.scheme.lower() == "bearer" else ""
    if not hmac.compare_digest(provided.encode(), token.encode()):
        raise HTTPException(401, "数据接入需要有效的 Bearer 令牌")


router = APIRouter(dependencies=[Depends(require_operator)])


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=3306, ge=1, le=65535)
    database: Identifier
    username: str = Field(min_length=1, max_length=64)
    tables: list[Identifier] = Field(min_length=1, max_length=100)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    timezone: str = Field(min_length=1, max_length=64)
    tls_ca: str | None = None


class TableMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity: str
    table: Identifier
    # 方向：中台字段名 -> 源表字段名。
    columns: dict[Identifier, Identifier] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def check_entity(self):
        if self.entity not in ENTITIES:
            raise ValueError("不支持的目标实体")
        if not ENTITIES[self.entity][1].issubset(self.columns):
            raise ValueError("缺少目标实体的必要字段，请查询 /contracts")
        return self


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # 客户端可预先生成 UUID hex，超时后凭此 ID 查询状态；相同 ID 不重复导入。
    job_id: str = Field(default_factory=lambda: uuid4().hex, pattern=r"^[0-9a-f]{32}$")
    mappings: list[TableMapping] = Field(min_length=1, max_length=8)
    max_rows_per_table: int = Field(default=100000, ge=1, le=500000)

    @model_validator(mode="after")
    def unique_entities(self):
        if len({m.entity for m in self.mappings}) != len(self.mappings):
            raise ValueError("目标实体不得重复")
        return self


def success(data):
    return {"code": 200, "message": "success", "data": data}


def load_sources():
    if not CONFIG_FILE.exists():
        return {}
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
        if not isinstance(raw, dict) or not all(re.fullmatch(SOURCE_ID, key) for key in raw):
            raise ValueError("invalid source id")
        return {key: Source.model_validate(value) for key, value in raw.items()}
    except (ValueError, OSError, TypeError):
        raise HTTPException(503, "sources.local.json 配置无效，请在服务器本地检查") from None


def get_source(source_id):
    source = load_sources().get(source_id)
    if source is None:
        raise HTTPException(404, "数据源不存在")
    return source


@asynccontextmanager
async def source_connection(source_id):
    source = get_source(source_id)
    password = os.getenv(f"DATAMIND_SOURCE_{source_id.upper()}_PASSWORD", "")
    if not password:
        raise HTTPException(503, "源库密码未配置，请在服务器本地配置对应环境变量")
    connection = None
    try:
        async with asyncio.timeout(TIMEOUT_SECONDS), _slots:
            tls = ssl.create_default_context(cafile=source.tls_ca) if source.tls_ca else None
            connection = await aiomysql.connect(
                host=source.host, port=source.port, user=source.username,
                password=password, db=source.database, charset="utf8mb4",
                connect_timeout=5, autocommit=True, local_infile=False, ssl=tls,
            )
            async with connection.cursor() as cursor:
                await cursor.execute("SET SESSION MAX_EXECUTION_TIME=15000")
                await cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            yield connection, source
    except HTTPException:
        raise
    except Exception:
        # 不返回驱动异常：错误中可能包含主机、用户名或凭据。
        raise HTTPException(502, "源库连接或读取失败，请检查地址、只读权限、TLS 与超时设置") from None
    finally:
        if connection is not None:
            # 所有路径均关闭连接，由 MySQL 回滚只读事务；不复用残留会话。
            connection.close()


async def table_columns(connection, source, table):
    if table not in source.tables:
        raise HTTPException(400, "源表不在服务器配置的允许列表中")
    async with connection.cursor(aiomysql.DictCursor) as cursor:
        await cursor.execute(
            "SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND TABLE_TYPE='BASE TABLE'",
            (source.database, table),
        )
        info = await cursor.fetchone()
        if not info or info["ENGINE"] != "InnoDB":
            raise HTTPException(400, "源表必须为存在的 InnoDB 基表，以保证一致性快照")
        await cursor.execute(
            "SELECT COLUMN_NAME AS name, DATA_TYPE AS type, IS_NULLABLE AS nullable "
            "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION",
            (source.database, table),
        )
        return await cursor.fetchall()


async def validate_mapping(connection, source, mapping):
    columns = await table_columns(connection, source, mapping.table)
    names = {column["name"] for column in columns}
    if not set(mapping.columns.values()).issubset(names):
        raise HTTPException(400, "字段映射包含不存在的源字段")


def select_sql(mapping):
    # 所有标识符由 Pydantic 限定，值参数化；不接受 SQL、过滤表达式或跨库查询。
    projection = ", ".join(f"`{origin}` AS `{target}`" for target, origin in mapping.columns.items())
    return f"SELECT {projection} FROM `{mapping.table}` LIMIT %s"


def csv_value(value):
    if isinstance(value, bytes):
        raise HTTPException(400, "不支持二进制字段，请调整字段映射")
    if isinstance(value, (datetime, date)):
        value = value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    result = "" if value is None else str(value)
    if len(result.encode("utf-8")) > MAX_CELL_BYTES:
        raise HTTPException(413, "字段超过 1 MiB，本次操作已撤销")
    return result


@router.get("/contracts")
async def contracts():
    return success({key: {"filename": value[0], "required_columns": sorted(value[1])} for key, value in ENTITIES.items()})


@router.get("")
async def list_sources():
    return success([{"source_id": key, "currency": src.currency, "timezone": src.timezone} for key, src in load_sources().items()])


@router.post("/{source_id}/test")
async def test_source(source_id: str):
    async with source_connection(source_id) as (connection, source):
        for table in source.tables:
            await table_columns(connection, source, table)
        return success({"connected": True, "tables_checked": len(source.tables), "read_only_transaction": True})


@router.get("/{source_id}/tables")
async def list_tables(source_id: str):
    async with source_connection(source_id) as (connection, source):
        result = []
        for table in source.tables:
            result.append({"table": table, "columns": await table_columns(connection, source, table)})
        return success(result)


@router.post("/{source_id}/preview")
async def preview(source_id: str, mapping: TableMapping):
    async with source_connection(source_id) as (connection, source):
        await validate_mapping(connection, source, mapping)
        async with connection.cursor() as cursor:
            await cursor.execute(select_sql(mapping), (20,))
            rows = await cursor.fetchall()
        # 以字符串表示金额，避免浮点转换；预览上限 20 行。
        data = [dict(zip(mapping.columns, map(csv_value, row))) for row in rows]
        if len(json.dumps(data, ensure_ascii=False).encode("utf-8")) > 2 * 1024 * 1024:
            raise HTTPException(413, "预览超过 2 MiB，请减少字段")
        return success({"entity": mapping.entity, "rows": data, "currency": source.currency, "timezone": source.timezone})


def save_manifest(directory, manifest):
    temp = directory / "manifest.tmp"
    temp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(directory / "manifest.json")


async def export_table(connection, mapping, directory, limit, used_bytes):
    filename = ENTITIES[mapping.entity][0]
    count = 0
    with (directory / filename).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(mapping.columns)
        async with connection.cursor(aiomysql.SSCursor) as cursor:
            await cursor.execute(select_sql(mapping), (limit + 1,))
            while True:
                rows = await cursor.fetchmany(500)
                if not rows:
                    break
                for row in rows:
                    count += 1
                    if count > limit:
                        raise HTTPException(413, "源表超过单表行数上限，本次快照已全部撤销；请先在源库准备有界业务表")
                    values = [csv_value(value) for value in row]
                    writer.writerow(values)
                    if used_bytes + stream.tell() > MAX_BYTES:
                        raise HTTPException(413, "快照超过 200 MiB，本次快照已撤销")
                stream.flush()
    return {"entity": mapping.entity, "filename": filename, "rows": count, "bytes": (directory / filename).stat().st_size}


@router.post("/{source_id}/imports")
async def import_source(source_id: str, request: ImportRequest):
    # 同步等待有限快照完成，任务状态落盘；客户端超时后可用 job_id 查询，不自动重试。
    get_source(source_id)
    job_id = request.job_id
    directory = STAGING / job_id
    try:
        directory.mkdir(parents=True)
    except FileExistsError:
        raise HTTPException(409, {"job_id": job_id, "error": "任务 ID 已存在，请查询状态或使用新的 ID"}) from None
    manifest = {"job_id": job_id, "source_id": source_id, "status": "running", "started_at": datetime.now(timezone.utc).isoformat(), "tables": [], "mappings": jsonable_encoder(request.mappings)}
    save_manifest(directory, manifest)
    _active_jobs.add(job_id)
    try:
        async with source_connection(source_id) as (connection, source):
            for mapping in request.mappings:
                await validate_mapping(connection, source, mapping)
            used = 0
            for mapping in request.mappings:
                result = await export_table(connection, mapping, directory, request.max_rows_per_table, used)
                used += result["bytes"]
                manifest["tables"].append(result)
            manifest.update(currency=source.currency, timezone=source.timezone, status="succeeded")
        manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
        save_manifest(directory, manifest)
    except (Exception, asyncio.CancelledError) as exc:
        for filename in directory.glob("*.csv"):
            filename.unlink(missing_ok=True)
        manifest.update(status="failed", tables=[], error=exc.detail if isinstance(exc, HTTPException) else "快照任务中断或本地保存失败")
        save_manifest(directory, manifest)
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise HTTPException(exc.status_code if isinstance(exc, HTTPException) else 500, {"job_id": job_id, "error": manifest["error"]}) from None
    finally:
        _active_jobs.discard(job_id)
    return success(manifest)


@router.get("/imports/{job_id}")
async def import_status(job_id: str):
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise HTTPException(404, "导入任务不存在")
    path = STAGING / job_id / "manifest.json"
    if not path.is_file():
        raise HTTPException(404, "导入任务不存在")
    result = json.loads(path.read_text(encoding="utf-8"))
    if result["status"] == "running" and job_id not in _active_jobs:
        result.update(status="interrupted", error="服务曾中断；此目录中的 CSV 不可作为成功快照使用，请重新发起导入")
    return success(result)
