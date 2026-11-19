# -*- coding: utf-8 -*-
"""PYTHONHASHSEED 稳定性探针：输出关键词与 Schema 检索表顺序的摘要哈希。"""
import sys, json, hashlib, os
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from agent.nodes.schema_retrieval import _extract_keywords

QUESTIONS = [
    "总用户数是多少？",
    "每日活跃用户数趋势",
    "2017年去重下单用户数是多少？",
    "品类GMV占比分布",
    "卖家排行前20",
    "GMV按月趋势和订单数",
    "那前三名呢",
    "客单价和营收",
]

keywords = {q: _extract_keywords(q) for q in QUESTIONS}

# 模拟 schema_retrieval_node 的「成功无匹配 + 业务失败」合并排序逻辑（纯函数，不依赖网络）
def simulated_order(keywords, hit_factory):
    table_hits = {}
    table_priority = {}
    for ki, kw in enumerate(keywords):
        data = hit_factory(kw)
        for ti, name in enumerate(data):
            table_hits.setdefault(name, {"table_name": name})
            cand = (ki, ti)
            if name not in table_priority or cand < table_priority[name]:
                table_priority[name] = cand
    return [t["table_name"] for t in sorted(table_hits.values(), key=lambda x: table_priority[x["table_name"]])]

def hit_factory(kw):
    # 固定的模拟命中集（顺序随关键词不同），验证排序稳定性不受 hash seed 影响
    mapping = {
        "total_customers": ["ads_gmv_overview", "dws_daily_active_users"],
        "active_user_count": ["dws_daily_active_users"],
        "user": ["dws_daily_active_users", "ads_gmv_overview"],
        "customer": ["ads_gmv_overview"],
        "GMV": ["ads_gmv_overview", "ads_gmv_trend", "ads_category_rank"],
        "order": ["ads_gmv_trend", "dws_daily_trade"],
        "category": ["ads_category_rank", "dws_category_summary", "dws_daily_trade"],
        "seller": ["ads_top_sellers"],
        "trend": ["ads_gmv_trend"],
        "rank": ["ads_top_sellers", "ads_category_rank"],
        "avg_order_value": ["ads_gmv_overview"],
    }
    return mapping.get(kw, [])

orders = {q: simulated_order(kws, hit_factory) for q, kws in keywords.items()}
payload = {"keywords": keywords, "orders": orders}
digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
print("PYTHONHASHSEED=", os.getenv("PYTHONHASHSEED"))
print(json.dumps(keywords, ensure_ascii=False))
print("DIGEST=", digest)
