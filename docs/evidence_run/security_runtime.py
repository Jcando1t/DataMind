# -*- coding: utf-8 -*-
"""真实执行层安全与保护验证（真实只读 MySQL，连接前拦截测试不执行破坏性 SQL）。
输出脱敏证据：不打印密码/连接串。
"""
import sys, json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from agent.nodes.sql_execution import sql_execution_node
from agent.sql_guard import prepare_readonly_query, SqlGuardError

EVID = ROOT / "docs/evidence_round2/security_runtime.json"
out = {"generated_at": datetime.now().isoformat(timespec="seconds"), "checks": []}

def rec(name, **kw):
    out["checks"].append({"name": name, **kw})
    print(name, "->", json.dumps(kw, ensure_ascii=False)[:220])

# 1) 真实会话保护：只读事务 + MAX_EXECUTION_TIME 回读
r = sql_execution_node({"sql": "SELECT total_gmv FROM ads_gmv_overview", "schema_context": "", "retry_count": 0})
rec("session_protection_verified", error_type=r.get("sql_error_type"),
    meta=r.get("sql_meta"), rows=len(r.get("sql_result") or []))

# 2) 真实最大行数：dws_daily_trade 有 18927 行，应被外层 LIMIT 1000 截断
r = sql_execution_node({"sql": "SELECT * FROM dws_daily_trade", "schema_context": "", "retry_count": 0})
rec("max_rows_cap", rows=len(r.get("sql_result") or []), truncated=r.get("result_truncated"),
    limit_action=(r.get("sql_meta") or {}).get("limit_action"))

# 3) 真实 LIMIT offset,count 语义保留
r = sql_execution_node({"sql": "SELECT `year_month`, monthly_gmv FROM ads_gmv_trend ORDER BY `year_month` LIMIT 3 OFFSET 5",
                        "schema_context": "", "retry_count": 0})
rows = r.get("sql_result") or []
rec("limit_offset", rows=len(rows), first=rows[0] if rows else None,
    limit_action=(r.get("sql_meta") or {}).get("limit_action"))

# 4) 策略拒绝必须在建立数据库连接前（连接前拦截，不执行任何破坏性 SQL）
DENIED = [
    "WITH ods_customers AS (SELECT 1 AS x) SELECT * FROM datamind_ods.ods_customers",
    "SELECT * FROM (SELECT total_gmv FROM ads_gmv_overview FOR UPDATE) t",
    "SELECT * FROM ads_gmv_overview UNION SELECT * FROM (SELECT total_gmv FROM ads_gmv_overview LOCK IN SHARE MODE) t",
    "SELECT * FROM mysql.user",
    "SELECT 1 INTO OUTFILE '/tmp/x'",
    "SELECT 1; SELECT 2",
]
for sql in DENIED:
    with patch("agent.nodes.sql_execution.pymysql.connect") as connect:
        rr = sql_execution_node({"sql": sql, "schema_context": "", "retry_count": 0})
        rec("denied_before_connect", sql=sql[:70], error_type=rr.get("sql_error_type"),
            connect_called=connect.called)

EVID.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print("\n证据:", EVID)
