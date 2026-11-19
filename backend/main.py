# -*- coding: utf-8 -*-
"""DataMind 数据中台 FastAPI 后端服务入口

启动方式（在 backend 目录下执行）：
    uvicorn main:app --reload --host 0.0.0.0 --port 8000

功能：
- 注册指标查询路由（/api/metrics）和数据字典检索路由（/api/schema）
- 启用 CORS（允许所有来源，开发阶段）
- 健康检查：GET /health
- 启动时打印所有已注册路由
- 统一响应格式：{"code": 200, "message": "success", "data": ...}
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.metrics import router as metrics_router
from api.schema_search import router as schema_router
from api.chat import router as chat_router
from api.sources import router as sources_router
from db.connection import ads_engine, dws_engine
from agent.redaction import sanitize_data, sanitize_text

# ==================== 日志配置 ====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("datamind")


# ==================== 应用生命周期 ====================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动与关闭钩子"""
    # 启动时打印所有已注册路由（含各子路由器内部的完整路径）
    logger.info("=" * 60)
    logger.info("DataMind API 已注册路由：")
    for router_obj, prefix in REGISTERED_ROUTERS:
        for route in router_obj.routes:
            methods = ",".join(sorted(getattr(route, "methods", []) or []))
            path = getattr(route, "path", str(route))
            logger.info("  [%s] %s%s", methods or "-", prefix, path)
    for route in app.routes:
        # 打印应用自身的路由（如 /health）
        if getattr(route, "path", None) in ("/health",):
            methods = ",".join(sorted(getattr(route, "methods", []) or []))
            logger.info("  [%s] %s", methods or "-", route.path)
    logger.info("=" * 60)
    yield
    # 关闭时释放连接池
    await ads_engine.dispose()
    await dws_engine.dispose()
    logger.info("数据库连接池已释放，服务已关闭")


# ==================== 创建 FastAPI 应用 ====================
app = FastAPI(
    title="DataMind API",
    description="DataMind 电商数据中台后端服务",
    version="1.0.0",
    lifespan=lifespan,
)

# ==================== CORS（开发阶段允许所有来源） ====================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],        # 允许所有来源
    allow_credentials=True,
    allow_methods=["*"],        # 允许所有方法
    allow_headers=["*"],        # 允许所有请求头
)

# ==================== 注册路由 ====================
# 记录 (路由器, 前缀) 供启动时打印路由使用
REGISTERED_ROUTERS = [
    (metrics_router, "/api/metrics"),
    (schema_router, "/api/schema"),
    (chat_router, "/api"),
    (sources_router, "/api/sources"),
]
app.include_router(metrics_router, prefix="/api/metrics", tags=["指标查询"])
app.include_router(schema_router, prefix="/api/schema", tags=["数据字典检索"])
app.include_router(chat_router, prefix="/api", tags=["智能问数"])
app.include_router(sources_router, prefix="/api/sources", tags=["MySQL 数据接入"])


# ==================== 统一异常处理 ====================
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """处理主动抛出的 HTTP 异常（如 404 表不存在），包装成统一错误格式"""
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.status_code, "message": sanitize_data(exc.detail), "data": None},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """处理请求参数校验错误（如缺少 q 参数）"""
    details = [{"loc": list(item["loc"]), "type": item["type"], "msg": item["msg"]} for item in exc.errors()]
    return JSONResponse(status_code=422, content={"code": 422, "message": "请求参数错误", "data": {"errors": sanitize_data(details)}})


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """兜底异常处理：所有未捕获异常统一返回 500 格式"""
    logger.error("接口发生未处理异常 %s：%s", type(exc).__name__, sanitize_text(str(exc)))
    return JSONResponse(
        status_code=500,
        content={"code": 500, "message": "服务器内部错误，请稍后重试", "data": None},
    )


# ==================== 健康检查 ====================
@app.get("/health", tags=["健康检查"])
async def health_check():
    """健康检查接口"""
    return {"status": "ok", "service": "DataMind API"}


if __name__ == "__main__":
    # 本地调试直接运行：python main.py
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
