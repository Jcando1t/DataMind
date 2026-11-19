# -*- coding: utf-8 -*-
"""MySQL 接入的路由与快照测试；假连接不接触业务库或现有数仓。"""
import csv
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient
import main
from api import sources
from agent.redaction import sanitize_text

TOKEN = "test-operator-token-" + "x" * 32
PASSWORD = "test-source-secret"
ORDER_COLUMNS = {
    "order_id": "id", "customer_id": "buyer_id", "order_status": "status",
    "order_purchase_timestamp": "created_at",
}
MAPPING = {"entity": "orders", "table": "orders", "columns": ORDER_COLUMNS}


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []
        self.offset = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, sql, params=None):
        self.connection.queries.append((sql, params))
        if "information_schema.TABLES" in sql:
            self.rows = [{"ENGINE": self.connection.engine}]
        elif "information_schema.COLUMNS" in sql:
            self.rows = [{"name": name, "type": "varchar", "nullable": "NO"} for name in ORDER_COLUMNS.values()]
        elif sql.startswith("SELECT"):
            self.rows = self.connection.rows[:params[0]]

    async def fetchone(self):
        return self.rows[0] if self.rows else None

    async def fetchall(self):
        return self.rows

    async def fetchmany(self, size):
        rows = self.rows[self.offset:self.offset + size]
        self.offset += len(rows)
        return rows


class FakeConnection:
    def __init__(self):
        self.rows = [("o1", "c1", "delivered", "2026-11-17 12:00:00")]
        self.queries = []
        self.engine = "InnoDB"
        self.closed = False

    def cursor(self, *args):
        return FakeCursor(self)

    def close(self):
        self.closed = True


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.config = self.directory / "sources.json"
        self.config.write_text(json.dumps({"shop": {
            "host": "localhost", "database": "shop", "username": "reader",
            "tables": ["orders"], "currency": "CNY", "timezone": "Asia/Shanghai",
        }}), encoding="utf-8")
        self.connection = FakeConnection()

        async def connect(**kwargs):
            self.connect_args = kwargs
            return self.connection

        for patcher in (
            patch.object(sources, "CONFIG_FILE", self.config),
            patch.object(sources, "STAGING", self.directory / "snapshots"),
            patch.object(sources.aiomysql, "connect", connect),
            patch.dict(os.environ, {"DATAMIND_INGEST_TOKEN": TOKEN, "DATAMIND_SOURCE_SHOP_PASSWORD": PASSWORD}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(main.app)
        self.headers = {"Authorization": f"Bearer {TOKEN}"}

    def post(self, suffix, body=None):
        return self.client.post("/api/sources/shop/" + suffix, json=body, headers=self.headers)

    def test_auth_and_disabled(self):
        self.assertEqual(self.client.get("/api/sources").status_code, 401)
        self.assertEqual(self.client.get("/api/sources", headers={"Authorization": "Bearer wrong"}).status_code, 401)
        with patch.dict(os.environ, {"DATAMIND_INGEST_TOKEN": ""}):
            self.assertEqual(self.client.get("/api/sources", headers=self.headers).status_code, 503)

    def test_config_and_contracts(self):
        response = self.client.get("/api/sources", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("username", response.text)
        self.assertNotIn(PASSWORD, response.text)
        contract = self.client.get("/api/sources/contracts", headers=self.headers).json()["data"]
        self.assertIn("price", contract["order_items"]["required_columns"])

    def test_read_only_connection_and_cleanup(self):
        response = self.post("test")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY", [sql for sql, _ in self.connection.queries])
        self.assertTrue(self.connection.closed)
        self.assertFalse(self.connect_args["local_infile"])

    def test_metadata(self):
        response = self.client.get("/api/sources/shop/tables", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"][0]["columns"][0]["name"], "id")

    def test_preview_mapped_fields_and_limit(self):
        self.connection.rows *= 30
        response = self.post("preview", MAPPING)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["data"]["rows"]), 20)
        self.assertEqual(response.json()["data"]["rows"][0]["order_id"], "o1")
        self.assertIn("`id` AS `order_id`", self.connection.queries[-1][0])

    def test_identifier_injection_and_required_fields(self):
        for mapping in (
            {**MAPPING, "table": "orders`; DROP TABLE orders;--"},
            {**MAPPING, "columns": {"order_id": "id"}},
            {**MAPPING, "columns": {**ORDER_COLUMNS, "order_id": "id FROM mysql.user"}},
        ):
            self.assertEqual(self.post("preview", mapping).status_code, 422)
        self.assertFalse(self.connection.queries)

    def test_whitelist_and_missing_column(self):
        self.assertEqual(self.post("preview", {**MAPPING, "table": "secret"}).status_code, 400)
        self.assertEqual(self.post("preview", {**MAPPING, "columns": {**ORDER_COLUMNS, "order_id": "missing"}}).status_code, 400)
        self.assertFalse(any(sql.startswith("SELECT `") for sql, _ in self.connection.queries))

    def test_nontransactional_table_rejected(self):
        self.connection.engine = "MyISAM"
        self.assertEqual(self.post("test").status_code, 400)

    def test_snapshot_and_duplicate_id(self):
        self.connection.rows = [("o,1\nsecond line", "c1", "delivered", "2026-11-17 12:00:00")]
        job_id = "a" * 32
        body = {"job_id": job_id, "mappings": [MAPPING]}
        response = self.post("imports", body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"]["status"], "succeeded")
        path = self.directory / "snapshots" / job_id / "olist_orders_dataset.csv"
        with path.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(rows[0]["order_id"], "o,1\nsecond line")
        self.assertEqual(self.post("imports", body).status_code, 409)
        status = self.client.get(f"/api/sources/imports/{job_id}", headers=self.headers)
        self.assertEqual(status.json()["data"]["tables"][0]["rows"], 1)

    def test_row_limit_fails_without_partial_csv(self):
        self.connection.rows *= 2
        response = self.post("imports", {"mappings": [MAPPING], "max_rows_per_table": 1})
        self.assertEqual(response.status_code, 413, response.text)
        job_id = response.json()["message"]["job_id"]
        directory = self.directory / "snapshots" / job_id
        self.assertFalse(list(directory.glob("*.csv")))
        self.assertEqual(json.loads((directory / "manifest.json").read_text(encoding="utf-8"))["status"], "failed")
        self.assertTrue(self.connection.closed)

    def test_size_limit_and_binary(self):
        with patch.object(sources, "MAX_BYTES", 1):
            self.assertEqual(self.post("imports", {"mappings": [MAPPING]}).status_code, 413)
        self.connection.rows = [(b"binary", "c1", "delivered", "2026-11-17")]
        self.assertEqual(self.post("preview", MAPPING).status_code, 400)
        self.connection.rows = [("x" * (sources.MAX_CELL_BYTES + 1), "c1", "delivered", "2026-11-17")]
        self.assertEqual(self.post("preview", MAPPING).status_code, 413)

    def test_bad_request_limits_and_duplicates(self):
        for body in (
            {"mappings": [MAPPING], "max_rows_per_table": 500001},
            {"mappings": [MAPPING, MAPPING]},
            {"mappings": [MAPPING], "job_id": "../escape"},
        ):
            self.assertEqual(self.post("imports", body).status_code, 422)

    def test_credentials_never_returned(self):
        async def failed_connect(**kwargs):
            raise sources.aiomysql.OperationalError(1045, f"failed password {PASSWORD}")
        with patch.object(sources.aiomysql, "connect", failed_connect):
            response = self.post("test")
        self.assertEqual(response.status_code, 502)
        self.assertNotIn(PASSWORD, response.text)
        self.assertNotIn(PASSWORD, sanitize_text(PASSWORD))
        self.assertNotIn(TOKEN, sanitize_text(TOKEN))

    def test_interrupted_job_and_path(self):
        job_id = "b" * 32
        directory = self.directory / "snapshots" / job_id
        directory.mkdir(parents=True)
        sources.save_manifest(directory, {"status": "running", "job_id": job_id})
        response = self.client.get(f"/api/sources/imports/{job_id}", headers=self.headers)
        self.assertEqual(response.json()["data"]["status"], "interrupted")
        self.assertEqual(self.client.get("/api/sources/imports/not-a-job", headers=self.headers).status_code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
