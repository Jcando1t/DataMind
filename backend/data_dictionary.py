# -*- coding: utf-8 -*-
"""DataMind 数据字典：7 张授权 ADS/DWS 表及准确业务口径。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datamind_config import database_name, load_dataset_config

_DATASET = load_dataset_config()
_CURRENCY = _DATASET["currency"]

# 当前静态快照说明；执行时从 overview 读取实际覆盖日期。
DATA_PERIOD_START = "2016-09-04" if _DATASET["database_prefix"] == "datamind" else None
DATA_PERIOD_END = "2018-09-03" if _DATASET["database_prefix"] == "datamind" else None

DATA_DICTIONARY = [
    {
        "table_name": "ads_gmv_overview",
        "table_desc": f"GMV总览，只有一行；数据覆盖日期以data_period字段为准；GMV口径为{_DATASET['gmv_definition']}",
        "database": database_name("ads", _DATASET),
        "fields": [
            {"name": "total_gmv", "desc": f"全周期总成交金额，单位{_CURRENCY}；口径：{_DATASET['gmv_definition']}"},
            {"name": "total_orders", "desc": "全周期去重订单总数，不使用品类订单数累加"},
            {"name": "avg_order_value", "desc": f"平均客单价（每单平均成交金额，{_CURRENCY}）"},
            {"name": "total_customers", "desc": "全周期全局去重下单客户总数，不是登录用户，不可由每日用户数累加得到"},
            {"name": "data_period_start", "desc": "实际数据起始日期，判断首月覆盖范围的依据"},
            {"name": "data_period_end", "desc": "实际数据截止日期，判断末月覆盖范围的依据"},
        ],
    },
    {
        "table_name": "ads_gmv_trend",
        "table_desc": "GMV月度趋势，按月份统计全局去重订单数、成交金额及环比；缺失月份补零；最近一期取所查询范围最大月份，月份覆盖参照overview的实际日期",
        "database": database_name("ads", _DATASET),
        "fields": [
            {"name": "year_month", "desc": "月份（格式如2017-01）"},
            {"name": "monthly_gmv", "desc": f"当月成交金额，单位{_CURRENCY}；口径：{_DATASET['gmv_definition']}"},
            {"name": "monthly_orders", "desc": "当月全局去重订单数，合计等于98207"},
            {"name": "mom_growth_rate", "desc": "GMV环比增长率，已是百分数，如107.06表示107.06%，不得再次乘100；首月或上月为零时为空"},
        ],
    },
    {
        "table_name": "ads_category_rank",
        "table_desc": "72个品类按GMV降序排行；品类订单可能跨品类重复，不能累加为全局订单数",
        "database": database_name("ads", _DATASET),
        "fields": [
            {"name": "rank", "desc": "品类排名（按GMV降序，1为最高）"},
            {"name": "category_english", "desc": "品类英文名称"},
            {"name": "total_gmv", "desc": f"该品类全周期成交金额，单位{_CURRENCY}；口径：{_DATASET['gmv_definition']}"},
            {"name": "total_orders", "desc": "该品类关联订单数，跨品类不可直接相加当作全局订单数"},
            {"name": "gmv_ratio", "desc": "该品类GMV占全局总GMV的百分数；TopN合计不等于100%，画全局饼图必须包含其余品类"},
        ],
    },
    {
        "table_name": "ads_top_sellers",
        "table_desc": f"按GMV降序的前20名卖家，包含城市和州；金额单位{_CURRENCY}，订单数与金额是不同单位",
        "database": database_name("ads", _DATASET),
        "fields": [
            {"name": "rank", "desc": "卖家排名（按GMV降序，1为最高，共20名）"},
            {"name": "seller_id", "desc": "卖家ID"},
            {"name": "seller_city", "desc": "卖家所在城市"},
            {"name": "seller_state", "desc": "卖家所在州"},
            {"name": "total_gmv", "desc": f"该卖家全周期成交金额，单位{_CURRENCY}；口径：{_DATASET['gmv_definition']}"},
            {"name": "total_orders", "desc": f"该卖家关联订单数，单位单；不得与{_CURRENCY}金额在同一未标单位的坐标轴混画"},
        ],
    },
    {
        "table_name": "dws_daily_trade",
        "table_desc": "每日品类交易汇总，按日期+品类粒度统计；适合时间序列与品类交叉分析，不适合直接累加为全局去重订单或客户",
        "database": database_name("dws", _DATASET),
        "fields": [
            {"name": "order_date", "desc": "订单日期（格式如2017-01-15）"},
            {"name": "category_english", "desc": "品类英文名称"},
            {"name": "order_count", "desc": "当日该品类关联订单数，跨品类可能重复，不可直接相加当作全局订单数"},
            {"name": "gmv", "desc": f"当日该品类成交金额，单位{_CURRENCY}；口径：{_DATASET['gmv_definition']}"},
            {"name": "avg_order_value", "desc": f"当日该品类平均客单价，单位{_CURRENCY}"},
        ],
    },
    {
        "table_name": "dws_daily_active_users",
        "table_desc": "每日去重下单用户数趋势，不是登录活跃；全量每日数值合计97277是用户日人次，不是全局去重客户数，也不能精确推导自定义期间去重客户数",
        "database": database_name("dws", _DATASET),
        "fields": [
            {"name": "order_date", "desc": "下单日期（格式如2017-01-15），覆盖数据周期内的日期"},
            {"name": "active_user_count", "desc": "当日去重下单用户数，单位人；跨日相加仅得用户日人次，不等于期间去重客户数"},
        ],
    },
    {
        "table_name": "dws_category_summary",
        "table_desc": "全周期品类汇总画像；品类订单可能重复，不能跨品类累加当作全局订单数",
        "database": database_name("dws", _DATASET),
        "fields": [
            {"name": "category_english", "desc": "品类英文名称"},
            {"name": "total_orders", "desc": "该品类全周期关联订单数，跨品类不可直接相加当作全局订单数"},
            {"name": "total_gmv", "desc": f"该品类全周期成交金额，单位{_CURRENCY}；口径：{_DATASET['gmv_definition']}"},
            {"name": "avg_price", "desc": f"该品类商品平均售价，单位{_CURRENCY}"},
            {"name": "seller_count", "desc": "该品类关联卖家数"},
        ],
    },
]
