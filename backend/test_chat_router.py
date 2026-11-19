# -*- coding: utf-8 -*-
"""路由级回归测试：/api/chat 的故障状态与成功状态

独立验收报告问题 4：数据库连接失败 / SQL 重试耗尽 仍返回 code=200 success。
本测试直接调用真实路由函数（TestClient），用假图替换 Agent 以注入故障，
不停止 MySQL、不调用模型。

断言：
- 连接失败 → HTTP 503 + 业务 code 503 + error_category=connection
- 被安全策略拒绝 → HTTP 400 + code 400
- SQL 失败 → HTTP 500 + code 500
- 成功空结果 → HTTP 200 + code 200（业务成功）
- 正常结果 → HTTP 200 + 数据/图表齐全
- 任何响应不得泄露数据库密码

运行方式：
    cd D:\\DataMind
    D:\\python3.12.10\\python.exe backend\\test_chat_router.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402  backend/main.py
from api import chat as chat_module  # noqa: E402
from agent.config import MYSQL_QUERY_PASSWORD  # noqa: E402


class FakeGraph:
    """用固定 state 替换 LangGraph，注入各类执行结果"""

    def __init__(self, override: dict):
        self.override = override

    def invoke(self, initial_state: dict) -> dict:
        return {**initial_state, **self.override}


def ask(override: dict):
    """以指定 Agent 输出调用 /api/chat，返回 (状态码, 响应体)"""
    original = chat_module.agent_graph
    chat_module.agent_graph = FakeGraph(override)
    try:
        with TestClient(main.app) as client:
            resp = client.post("/api/chat", json={"question": "总 GMV 是多少？"})
        return resp.status_code, resp.json()
    finally:
        chat_module.agent_graph = original


def test_connection_failure():
    status, body = ask(
        {
            "final_answer": "数据库连接失败，无法执行查询。",
            "response_type": "text",
            "sql_error": "数据库连接失败（错误码 2003）",
            "sql_error_type": "connection",
            "error_category": "connection",
        }
    )
    assert status == 503, status
    assert body["code"] == 503, body
    assert body["message"] != "success", body
    assert body["data"]["error_category"] == "connection", body
    print("  1. 连接失败 → 503/业务 503 通过")


def test_guard_rejection():
    status, body = ask(
        {
            "final_answer": "该查询被安全策略拒绝",
            "sql_error": "安全限制：禁止访问库 mysql",
            "sql_error_type": "guard",
            "error_category": "guard",
        }
    )
    assert status == 400, status
    assert body["code"] == 400, body
    print("  2. 安全策略拒绝 → 400 通过")


def test_sql_failure():
    status, body = ask(
        {
            "final_answer": "SQL 执行失败（已自动重试仍未成功）",
            "sql_error": "MySQL 执行错误（错误码 1064）",
            "sql_error_type": "sql",
            "error_category": "sql",
        }
    )
    assert status == 500, status
    assert body["code"] == 500, body
    assert body["data"]["sql_error"] == "MySQL 执行错误（错误码 1064）", body
    print("  3. SQL 失败 → 500 通过")


def test_empty_result_is_success():
    status, body = ask(
        {
            "final_answer": "查询执行成功，但没有匹配到任何数据。",
            "response_type": "text",
            "sql_result": [],
            "sql_error": None,
            "error_category": None,
        }
    )
    assert status == 200, status
    assert body["code"] == 200 and body["message"] == "success", body
    assert body["data"]["row_count"] == 0, body
    assert body["data"]["error_category"] is None, body
    print("  4. 成功空结果 → 200 通过")


def test_normal_result():
    rows = [
        {"year_month": "2018-08", "monthly_gmv": 1.0},
        {"year_month": "2018-09", "monthly_gmv": 2.0},
    ]
    status, body = ask(
        {
            "final_answer": "2018-09 为最新月份。",
            "response_type": "chart",
            "chart_config": {"type": "line", "x": "year_month", "y": ["monthly_gmv"]},
            "sql": "SELECT 1",
            "sql_result": rows,
            "result_truncated": False,
            "error_category": None,
        }
    )
    assert status == 200, status
    assert body["data"]["row_count"] == 2, body
    assert body["data"]["chart_config"]["x"] == "year_month", body
    assert body["data"]["result_truncated"] is False, body
    print("  5. 正常查询 → 200 且数据完整 通过")


def test_no_credential_leak():
    """即便内部错误文本含密码，也不得出现在响应里"""
    status, body = ask(
        {
            "final_answer": f"连接串 user=datamind_ro password={MYSQL_QUERY_PASSWORD} 失败",
            "sql_error": f"password={MYSQL_QUERY_PASSWORD}",
            "sql_error_type": "connection",
            "error_category": "connection",
        }
    )
    raw = str(body)
    assert MYSQL_QUERY_PASSWORD not in raw, "响应泄露了数据库密码"
    assert "***" in body["data"]["answer"], body
    print("  6. 响应凭据脱敏 通过")


def test_review_insight_payload_and_nested_redaction():
    """评论统计、原文引用与检索元数据必须完整进入 /api/chat 响应。"""
    secret = MYSQL_QUERY_PASSWORD or "synthetic-review-secret"
    insight = {
        "stats": {"total_reviews": 10, "low_score_reviews": 3, "low_score_ratio": 30.0},
        "matching_stats": {"total_reviews": 3},
        "citations": [{
            "citation_id": "R1", "review_id": "review-1", "order_id": "order-1",
            "score": 1, "text": f"评论原文 password={secret}",
        }],
        "retrieval": {"method": "sqlite_fts5_bm25_rrf", "queries": ["entrega atrasada"]},
    }
    status, body = ask({
        "final_answer": "低分评价 3 条。\n\n配送延迟。[R1]",
        "response_type": "review_insight",
        "review_insight": insight,
        "error_category": None,
    })
    assert status == 200, status
    data = body["data"]
    assert data["response_type"] == "review_insight", data
    assert data["review_insight"]["stats"]["low_score_reviews"] == 3, data
    assert data["review_insight"]["citations"][0]["citation_id"] == "R1", data
    assert data["review_insight"]["retrieval"]["method"] == "sqlite_fts5_bm25_rrf", data
    if MYSQL_QUERY_PASSWORD:
        assert MYSQL_QUERY_PASSWORD not in str(body), "评论原文泄露了数据库密码"
        assert "***" in data["review_insight"]["citations"][0]["text"], data
    print("  8. 评论洞察响应完整且嵌套脱敏 通过")


def test_health():
    with TestClient(main.app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200 and resp.json()["status"] == "ok", resp.text
    print("  7. /health 正常 通过")


if __name__ == "__main__":
    print("路由级回归测试：")
    test_connection_failure()
    test_guard_rejection()
    test_sql_failure()
    test_empty_result_is_success()
    test_normal_result()
    test_no_credential_leak()
    test_review_insight_payload_and_nested_redaction()
    test_health()
    print()
    print("路由级测试全部通过")
