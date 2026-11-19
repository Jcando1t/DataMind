# -*- coding: utf-8 -*-
"""DataMind 数据集展示、业务口径与数仓库名配置。"""
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "backend" / "dataset.local.json"
DEFAULTS = {
    "display_name": "Olist 电商数据",
    "region": "Brazil",
    "currency": "BRL",
    "currency_symbol": "R$",
    "currency_locale": "zh-CN",
    "currency_label": "巴西雷亚尔",
    "timezone": "UTC",
    "database_prefix": "datamind",
    "gmv_definition": "商品金额 + 运费",
    "exclude_order_statuses": ["unavailable", "canceled"],
    "raw_dir": str(ROOT / "data" / "raw"),
}


def load_dataset_config(path: Path | None = None) -> dict:
    """加载本地数据集配置，并允许 ETL 命令用环境变量做单次覆盖。"""
    config = DEFAULTS.copy()
    config["exclude_order_statuses"] = list(DEFAULTS["exclude_order_statuses"])
    config_path = path or CONFIG_PATH
    if config_path.is_file():
        try:
            values = json.loads(config_path.read_text(encoding="utf-8-sig"))
            if not isinstance(values, dict):
                raise ValueError
            unknown = set(values) - set(DEFAULTS)
            if unknown:
                raise ValueError(f"未识别的配置项：{', '.join(sorted(unknown))}")
            config.update(values)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"数据集配置无效：{config_path}") from exc
    if os.getenv("DATAMIND_DATABASE_PREFIX"):
        config["database_prefix"] = os.environ["DATAMIND_DATABASE_PREFIX"]
    if os.getenv("DATAMIND_RAW_DIR"):
        config["raw_dir"] = os.environ["DATAMIND_RAW_DIR"]
    if os.getenv("DATAMIND_EXCLUDED_ORDER_STATUSES") is not None:
        config["exclude_order_statuses"] = [
            value.strip() for value in os.environ["DATAMIND_EXCLUDED_ORDER_STATUSES"].split(",") if value.strip()
        ]
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", str(config["database_prefix"])):
        raise ValueError("database_prefix 只允许小写字母、数字和下划线，并以字母开头")
    if not re.fullmatch(r"[A-Z]{3}", str(config["currency"])):
        raise ValueError("currency 必须是 ISO 4217 三字母代码")
    if not isinstance(config["currency_symbol"], str) or not config["currency_symbol"]:
        raise ValueError("currency_symbol 不能为空")
    if not isinstance(config["exclude_order_statuses"], list) or not all(isinstance(value, str) for value in config["exclude_order_statuses"]):
        raise ValueError("exclude_order_statuses 必须是字符串数组")
    config["raw_dir"] = str(Path(config["raw_dir"]).expanduser().resolve())
    return config


def database_name(layer: str, config: dict | None = None) -> str:
    if layer not in {"ods", "dwd", "dws", "ads"}:
        raise ValueError(f"未知数仓层：{layer}")
    return f"{(config or load_dataset_config())['database_prefix']}_{layer}"
