# -*- coding: utf-8 -*-
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from datamind_config import database_name, load_dataset_config


class DatasetConfigTests(unittest.TestCase):
    def test_company_config_sets_currency_status_and_isolated_databases(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "dataset.json"
            path.write_text(json.dumps({
                "display_name": "示例公司",
                "currency": "CNY",
                "currency_symbol": "¥",
                "currency_label": "人民币",
                "database_prefix": "datamind_company",
                "exclude_order_statuses": ["canceled", "cancelled"],
                "raw_dir": temp,
            }), encoding="utf-8")
            config = load_dataset_config(path)
        self.assertEqual(config["currency"], "CNY")
        self.assertEqual(config["exclude_order_statuses"], ["canceled", "cancelled"])
        self.assertEqual(database_name("ads", config), "datamind_company_ads")
        self.assertEqual(database_name("ods", config), "datamind_company_ods")

    def test_etl_overrides_are_scoped_to_the_process_environment(self):
        with patch.dict(os.environ, {
            "DATAMIND_DATABASE_PREFIX": "datamind_test",
            "DATAMIND_RAW_DIR": ".\\data\\test",
            "DATAMIND_EXCLUDED_ORDER_STATUSES": "void, canceled",
        }):
            config = load_dataset_config(Path("missing.json"))
        self.assertEqual(config["database_prefix"], "datamind_test")
        self.assertEqual(config["exclude_order_statuses"], ["void", "canceled"])

    def test_bad_currency_or_database_prefix_is_rejected(self):
        for content in ({"currency": "yuan"}, {"database_prefix": "company; drop database mysql"}):
            with tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "dataset.json"
                path.write_text(json.dumps(content), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_dataset_config(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
