# -*- coding: utf-8 -*-
"""真实模型联调（调用本机 8000 端口 /api/chat，真实 DeepSeek + 真实只读 MySQL）。
输出脱敏证据 JSON：仅记录 SQL/answer/chart_config/状态码，无任何凭据。
"""
import json
import time
from datetime import datetime
from pathlib import Path

import httpx

BASE = "http://localhost:8000"
ROOT = Path(__file__).resolve().parents[2]
EVID = ROOT / "docs/evidence_round2/live_chat.json"

# 触发词：这些是敏感凭据的已知前缀，做一次兜底扫描（真实值不从环境变量读取）
FORBIDDEN = ["sk-", "password=", "DATAMIND_QUERY_PASSWORD=", "DEEPSEEK_API_KEY="]

def norm_secret_scan(obj):
    text = json.dumps(obj, ensure_ascii=False)
    return [f for f in FORBIDDEN if f in text]

def call(client, question, history=None):
    payload = {"question": question}
    if history is not None:
        payload["chat_history"] = history
    t0 = time.time()
    resp = client.post(f"{BASE}/api/chat", json=payload, timeout=120)
    elapsed = round(time.time() - t0, 2)
    try:
        body = resp.json()
    except Exception:
        body = {"raw": resp.text[:500]}
    data = (body.get("data") or {}) if isinstance(body, dict) else {}
    rec = {
        "question": question,
        "history_used": bool(history),
        "http_status": resp.status_code,
        "code": body.get("code") if isinstance(body, dict) else None,
        "answer": data.get("answer"),
        "response_type": data.get("response_type"),
        "chart_config": data.get("chart_config"),
        "sql": data.get("sql"),
        "row_count": data.get("row_count"),
        "result_truncated": data.get("result_truncated"),
        "error_category": data.get("error_category"),
        "elapsed_s": elapsed,
    }
    rec["secret_hits"] = norm_secret_scan(rec)
    return rec

def main():
    results = []
    with httpx.Client() as client:
        # 基础问数
        results.append(call(client, "总体GMV是多少？"))
        results.append(call(client, "GMV按月的趋势怎么样？"))
        results.append(call(client, "哪个品类销售额最高？"))
        results.append(call(client, "卖家销售额排行前20"))
        results.append(call(client, "数据集中最后一个月的订单数是多少？"))
        # 总用户数（C 项修复重点）
        results.append(call(client, "总用户数是多少？"))
        # 每日活跃用户趋势
        results.append(call(client, "每日活跃用户数趋势"))
        # 无法精确回答：应明确能力不足
        results.append(call(client, "2017年去重下单用户数是多少？"))
        # 带上下文连续追问：品类 → 前三名
        cat = "哪个品类销售额最高？"
        results.append(call(client, cat))
        results.append(call(client, "那前三名呢？", history=[
            {"role": "user", "content": cat},
            {"role": "assistant", "content": "health_beauty 品类销售额最高。"},
        ]))
        # 切换指标后的追问：GMV趋势 → 订单数按月
        results.append(call(client, "那订单数按月呢？", history=[
            {"role": "user", "content": "GMV按月的趋势怎么样？"},
            {"role": "assistant", "content": "GMV整体呈上升趋势。"},
        ]))
        # 空白问题：应 422
        blank = client.post(f"{BASE}/api/chat", json={"question": "   \n "}, timeout=30)
        results.append({
            "question": "<空白问题>", "http_status": blank.status_code,
            "code": blank.json().get("code") if blank.status_code != 422 else 422,
            "_note": "F项输入校验，应在模型/数据库调用前返回422",
        })

    evidence = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "真实模型联调（本机8000端口，真实DeepSeek，真实只读MySQL）",
        "results": results,
    }
    EVID.parent.mkdir(parents=True, exist_ok=True)
    EVID.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")

    # 控制台摘要（脱敏）
    for r in results:
        print("=" * 70)
        print("Q:", r["question"])
        print(f"  HTTP={r.get('http_status')} code={r.get('code')} type={r.get('response_type')} "
              f"rows={r.get('row_count')} err={r.get('error_category')} t={r.get('elapsed_s')}s")
        print("  SQL:", (r.get("sql") or "")[:180])
        print("  ANS:", (r.get("answer") or "")[:200])
        if r.get("chart_config"):
            print("  CHART:", r.get("chart_config"))
        if r.get("secret_hits"):
            print("  !! SECRET HITS:", r["secret_hits"])
    print("\n证据文件:", EVID)

if __name__ == "__main__":
    main()
