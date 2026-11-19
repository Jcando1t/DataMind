# -*- coding: utf-8 -*-
"""集中返修：请求校验、完整响应脱敏与稳定异常响应测试（全部使用合成标记）。"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
import main
from api import chat as chat_module


class NoCallGraph:
    def invoke(self, state):
        raise AssertionError("无效请求不应进入 Agent/模型/数据库")


class FakeGraph:
    def __init__(self, override=None, error=None):
        self.override = override or {}
        self.error = error

    def invoke(self, state):
        if self.error:
            raise self.error
        return {**state, **self.override}


class ChatValidationTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self.original = chat_module.agent_graph
        chat_module.agent_graph = NoCallGraph()

    def tearDown(self):
        chat_module.agent_graph = self.original
        self.client.close()

    def test_invalid_requests_return_422_before_agent(self):
        cases = [
            {"question": "   \n\t"},
            {"question": "x" * 501},
            {"question": "正常", "chat_history": [{"role": "system", "content": "bad"}]},
            {"question": "正常", "chat_history": [{"role": "user", "content": "x" * 2001}]},
            {"question": "正常", "chat_history": [{"role": "user", "content": "x"}] * 21},
            {"question": "正常", "chat_history": [{"role": "user", "content": "x", "extra": "bad"}]},
        ]
        for payload in cases:
            with self.subTest(payload=str(payload)[:100]):
                response = self.client.post("/api/chat", json=payload)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["code"], 422)

    def test_valid_request_is_trimmed_and_history_is_typed(self):
        captured = {}
        class CaptureGraph:
            def invoke(self, state):
                captured.update(state)
                return {**state, "final_answer": "ok", "sql_result": [], "error_category": None}
        chat_module.agent_graph = CaptureGraph()
        response = self.client.post("/api/chat", json={
            "question": "  总GMV是多少？  ",
            "chat_history": [{"role": "user", "content": "  上一轮  "}],
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured["user_question"], "总GMV是多少？")
        self.assertEqual(captured["chat_history"], [{"role": "user", "content": "上一轮"}])


class ChatRedactionTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app, raise_server_exceptions=False)
        self.original = chat_module.agent_graph
        self.env = patch.dict("os.environ", {
            "DATAMIND_QUERY_PASSWORD": "synthetic-db-password@:#/",
            "DEEPSEEK_API_KEY": "synthetic-model-key",
        })
        self.env.start()

    def tearDown(self):
        self.env.stop()
        chat_module.agent_graph = self.original
        self.client.close()

    def assert_no_synthetic_secret(self, body):
        raw = json.dumps(body, ensure_ascii=False)
        self.assertNotIn("synthetic-db-password", raw)
        self.assertNotIn("synthetic-model-key", raw)

    def test_all_payload_fields_are_redacted(self):
        chat_module.agent_graph = FakeGraph({
            "final_answer": "bad synthetic-model-key",
            "sql": "SELECT 'synthetic-db-password@:#/' AS note",
            "sql_result": [{"note": "synthetic-model-key"}],
            "chart_config": {"label": "synthetic-db-password@:#/"},
            "error_category": None,
        })
        response = self.client.post("/api/chat", json={"question": "测试"})
        self.assert_no_synthetic_secret(response.json())

    def test_agent_unhandled_exception_is_stable_and_logs_are_redacted(self):
        chat_module.agent_graph = FakeGraph(error=RuntimeError("internal synthetic-model-key"))
        with self.assertLogs("datamind", level="ERROR") as logs:
            response = self.client.post("/api/chat", json={"question": "测试"})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["message"], "智能问数服务暂时不可用，请稍后重试")
        self.assert_no_synthetic_secret(response.json())
        self.assertNotIn("synthetic-model-key", "\n".join(logs.output))

    def test_global_exception_response_and_log_are_redacted(self):
        path = "/__synthetic_unhandled__"
        async def broken():
            raise RuntimeError("internal synthetic-db-password@:#/")
        main.app.add_api_route(path, broken)
        with self.assertLogs("datamind", level="ERROR") as logs:
            response = self.client.get(path)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["message"], "服务器内部错误，请稍后重试")
        self.assert_no_synthetic_secret(response.json())
        self.assertNotIn("synthetic-db-password", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main(verbosity=2)
