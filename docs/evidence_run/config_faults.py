# -*- coding: utf-8 -*-
"""配置故障与模型异常验证（模拟/故障注入，不输出任何真实凭据）。"""
import sys, json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from agent.config import require_api_key, require_query_password
from agent.nodes.sql_execution import sql_execution_node
from agent import llm as llm

EVID = ROOT / "docs/evidence_round2/config_faults.json"
out = {"generated_at": datetime.now().isoformat(timespec="seconds"), "checks": []}
def rec(name, **kw):
    out["checks"].append({"name": name, **kw})
    print(name, "->", json.dumps(kw, ensure_ascii=False)[:300])

# 1) 缺失 API Key：明确报错且不含任何真实密钥
with patch("agent.config.DEEPSEEK_API_KEY", ""):
    try:
        require_api_key(); msg = "NO_ERROR"
    except RuntimeError as e:
        msg = str(e)
    rec("missing_api_key", message=msg, leaks_secret=("sk-" in msg))

# 2) 缺失查询密码：明确报错且不含密码值
with patch("agent.config.MYSQL_QUERY_PASSWORD", ""):
    try:
        require_query_password(); msg = "NO_ERROR"
    except RuntimeError as e:
        msg = str(e)
    rec("missing_query_password", message=msg)

# 3) 错误端口：连接失败分类为 connection，不重试
with patch("agent.nodes.sql_execution.MYSQL_PORT", 3399):
    r = sql_execution_node({"sql": "SELECT total_gmv FROM ads_gmv_overview", "schema_context": "", "retry_count": 0})
    rec("wrong_port", error_type=r.get("sql_error_type"), error_shown=(r.get("sql_error") or "")[:80])

# 4) 模型超时：APITimeoutError 被转为稳定 LLMError（模拟，不真实消耗调用）
from openai import APITimeoutError
class FakeReq: pass
timeout_exc = APITimeoutError(request=FakeReq())
client = type("C", (), {"chat": type("Chat", (), {"completions": type("Comp", (), {
    "create": staticmethod(lambda **kw: (_ for _ in ()).throw(timeout_exc))})()})()})()
with patch.object(llm, "get_client", return_value=client):
    try:
        llm.chat([{"role":"user","content":"x"}]); msg="NO_ERROR"
    except llm.LLMError as e:
        msg=str(e)
    rec("model_timeout", message=msg)

# 5) 输出截断：finish_reason=length 被拒绝（模拟）
resp = type("R", (), {"choices":[type("Ch", (), {
    "message": type("M", (), {"content":"截断内容"})(), "finish_reason":"length"})()]})()
client2 = type("C", (), {"chat": type("Chat", (), {"completions": type("Comp", (), {
    "create": staticmethod(lambda **kw: resp)})()})()})()
with patch.object(llm, "get_client", return_value=client2):
    try:
        llm.chat([{"role":"user","content":"x"}]); msg="NO_ERROR"
    except llm.LLMOutputError as e:
        msg=str(e)
    rec("output_truncation", message=msg)

EVID.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print("\n证据:", EVID)
