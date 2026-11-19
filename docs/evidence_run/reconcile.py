# -*- coding: utf-8 -*-
"""只读账号直接查库对账（脱敏输出，仅打印聚合与基准核对，不打印任何凭据）。"""
import json, sys
from pathlib import Path
from decimal import Decimal

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import pymysql
from agent.config import MYSQL_HOST, MYSQL_PORT, MYSQL_QUERY_USER, require_query_password

def dec(v):
    return float(v) if hasattr(v, "quantize") else v

def q(cur, sql):
    cur.execute(sql)
    row = cur.fetchone()
    if row and isinstance(row, dict):
        return {k: dec(v) for k, v in row.items()}
    return row

conn = pymysql.connect(host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_QUERY_USER,
    password=require_query_password(), charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
    connect_timeout=10, autocommit=True)
out = {}
with conn:
    with conn.cursor() as c:
        out["current_user"] = q(c, "SELECT CURRENT_USER() AS u")["u"]
        grants = q(c, "SHOW GRANTS")  # 仅返回单行，改用下方 all
        c.execute("SHOW GRANTS")
        out["grants"] = [list(r.values())[0] for r in c.fetchall()]
        out["overview"] = q(c, "SELECT total_gmv, total_orders, avg_order_value, total_customers, "
            "data_period_start, data_period_end FROM datamind_ads.ads_gmv_overview LIMIT 1")
        out["trend"] = q(c, "SELECT COUNT(*) AS n, ROUND(SUM(monthly_gmv),2) AS gmv_sum, "
            "SUM(monthly_orders) AS order_sum, MIN(`year_month`) AS mn, MAX(`year_month`) AS mx, "
            "SUM(CASE WHEN monthly_gmv=0 THEN 1 ELSE 0 END) AS zero_months "
            "FROM datamind_ads.ads_gmv_trend")
        out["category"] = q(c, "SELECT COUNT(*) AS n, ROUND(SUM(total_gmv),2) AS gmv_sum, "
            "SUM(total_orders) AS order_sum, ROUND(SUM(gmv_ratio),2) AS ratio_sum "
            "FROM datamind_ads.ads_category_rank")
        out["top3"] = [ {k: dec(v) for k,v in r.items()} for r in (
            (c.execute("SELECT `rank`, category_english, total_gmv, gmv_ratio FROM datamind_ads.ads_category_rank ORDER BY `rank` LIMIT 3"), c.fetchall())[1])]
        out["sellers"] = q(c, "SELECT COUNT(*) AS n, ROUND(SUM(total_gmv),2) AS gmv_sum "
            "FROM datamind_ads.ads_top_sellers")
        out["daily_active_users"] = q(c, "SELECT COUNT(*) AS n, SUM(active_user_count) AS daily_sum, "
            "MIN(order_date) AS mn, MAX(order_date) AS mx FROM datamind_dws.dws_daily_active_users")
        out["daily_trade"] = q(c, "SELECT COUNT(*) AS n FROM datamind_dws.dws_daily_trade")
        out["category_summary"] = q(c, "SELECT COUNT(*) AS n FROM datamind_dws.dws_category_summary")

Path(ROOT / "docs/evidence_round2/mysql_reconcile.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
