# -*- coding: utf-8 -*-
"""ReviewStore 的离线单元测试，仅使用临时目录和微型 CSV。"""
from __future__ import annotations

import csv
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.review_store import ReviewStore


class ReviewStoreTests(unittest.TestCase):
    """覆盖评论关联、聚合、过滤、全文检索与自动重建。"""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        base = Path(self.temp_dir.name)
        self.raw_dir = base / "raw"
        self.raw_dir.mkdir()
        self.index_path = base / "index" / "reviews.sqlite3"
        self._write_sources()
        self.store = ReviewStore(index_path=self.index_path, raw_dir=self.raw_dir)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_csv(self, name: str, fieldnames: list[str], rows: list[dict]) -> None:
        with (self.raw_dir / name).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def _write_sources(self) -> None:
        self._write_csv(
            "product_category_name_translation.csv",
            ["product_category_name", "product_category_name_english"],
            [
                {"product_category_name": "cama_mesa_banho", "product_category_name_english": "bed_bath_table"},
                {"product_category_name": "beleza_saude", "product_category_name_english": "health_beauty"},
            ],
        )
        self._write_csv(
            "olist_products_dataset.csv",
            ["product_id", "product_category_name"],
            [
                {"product_id": "p1", "product_category_name": "cama_mesa_banho"},
                {"product_id": "p2", "product_category_name": "beleza_saude"},
            ],
        )
        self._write_csv(
            "olist_order_items_dataset.csv",
            ["order_id", "product_id"],
            [
                {"order_id": "o1", "product_id": "p1"},
                {"order_id": "o1", "product_id": "p2"},
                {"order_id": "o1", "product_id": "p1"},
                {"order_id": "o2", "product_id": "p2"},
                {"order_id": "o3", "product_id": "p1"},
                {"order_id": "o4", "product_id": "p2"},
            ],
        )
        self._write_csv(
            "olist_orders_dataset.csv",
            ["order_id", "order_purchase_timestamp"],
            [
                {"order_id": "o1", "order_purchase_timestamp": "2017-01-10 08:00:00"},
                {"order_id": "o2", "order_purchase_timestamp": "2017-02-01 09:00:00"},
                {"order_id": "o3", "order_purchase_timestamp": "2018-03-01 10:00:00"},
                {"order_id": "o4", "order_purchase_timestamp": "2018-04-01 11:00:00"},
            ],
        )
        self._write_csv(
            "olist_order_reviews_dataset.csv",
            ["review_id", "order_id", "review_score", "review_comment_title", "review_comment_message", "review_creation_date"],
            [
                {
                    "review_id": "r1",
                    "order_id": "o1",
                    "review_score": 1,
                    "review_comment_title": "Produto ruim",
                    "review_comment_message": "Entrega atrasada e produto quebrado",
                    "review_creation_date": "2017-01-12 00:00:00",
                },
                {
                    "review_id": "r2",
                    "order_id": "o2",
                    "review_score": 5,
                    "review_comment_title": "Excelente",
                    "review_comment_message": "Produto excelente, recomendo!",
                    "review_creation_date": "2017-02-03 00:00:00",
                },
                {
                    "review_id": "r3",
                    "order_id": "o3",
                    "review_score": 2,
                    "review_comment_title": "   ",
                    "review_comment_message": "",
                    "review_creation_date": "2018-03-02 00:00:00",
                },
                {
                    "review_id": "r4",
                    "order_id": "o4",
                    "review_score": 4,
                    "review_comment_title": "Good quality",
                    "review_comment_message": "Very good quality and fast delivery",
                    "review_creation_date": "2018-04-02 00:00:00",
                },
                {
                    "review_id": "r5",
                    "order_id": "o1",
                    "review_score": 3,
                    "review_comment_title": "Entrega",
                    "review_comment_message": "Entrega atrasada",
                    "review_creation_date": "2017-01-15 00:00:00",
                },
            ],
        )

    def test_build_index_and_join_metadata(self) -> None:
        status = self.store.status()
        self.assertTrue(status["ready"])
        self.assertEqual(status["document_count"], 5)
        self.assertEqual(status["text_document_count"], 4)
        self.assertIsNotNone(status["indexed_at"])

        result = self.store.search("quebrado", limit=1)[0]
        self.assertEqual(result["review_id"], "r1")
        self.assertEqual(result["order_id"], "o1")
        self.assertEqual(result["order_date"], "2017-01-10 08:00:00")
        self.assertEqual(result["categories"], ["bed_bath_table", "health_beauty"])

    def test_stats_and_low_score_ratio(self) -> None:
        result = self.store.stats()
        self.assertEqual(result["total_reviews"], 5)
        self.assertEqual(result["text_reviews"], 4)
        self.assertEqual(result["average_score"], 3.0)
        self.assertEqual(result["low_score_reviews"], 2)
        self.assertEqual(result["low_score_ratio"], 40.0)
        self.assertEqual(result["positive_score_reviews"], 2)
        self.assertEqual(
            result["score_distribution"],
            [
                {"score": 1, "count": 1},
                {"score": 2, "count": 1},
                {"score": 3, "count": 1},
                {"score": 4, "count": 1},
                {"score": 5, "count": 1},
            ],
        )
        self.assertEqual(result["date_start"], "2017-01-12")
        self.assertEqual(result["date_end"], "2018-04-02")
        self.assertEqual(result["applied_filters"], {})

    def test_score_date_and_category_filters(self) -> None:
        self.assertEqual(self.store.stats({"min_score": 4})["total_reviews"], 2)
        self.assertEqual(self.store.stats({"max_score": 2})["total_reviews"], 2)
        self.assertEqual(self.store.stats({"score": 3})["total_reviews"], 1)
        self.assertEqual(self.store.stats({"year": 2017})["total_reviews"], 3)
        self.assertEqual(self.store.stats({"month": 1})["total_reviews"], 2)
        date_filtered = self.store.stats({"start_date": "2017-02-01", "end_date": "2018-03-31"})
        self.assertEqual(date_filtered["total_reviews"], 2)
        self.assertEqual(self.store.stats({"category": "bed_bath_table"})["total_reviews"], 3)
        self.assertEqual(self.store.stats({"category": "bed"})["total_reviews"], 0)
        combined = self.store.stats({"category": "health_beauty", "year": "2017"})
        self.assertEqual(combined["total_reviews"], 3)
        self.assertEqual(combined["applied_filters"], {"year": 2017, "category": "health_beauty"})

    def test_fts_rrf_original_text_and_like_fallback(self) -> None:
        results = self.store.search(["entrega atrasada", "produto quebrado"], limit=5)
        self.assertEqual(results[0]["review_id"], "r1")
        self.assertEqual(results[0]["title"], "Produto ruim")
        self.assertEqual(results[0]["text"], "Entrega atrasada e produto quebrado")
        self.assertGreater(results[0]["retrieval_score"], 0)
        self.assertEqual(
            set(results[0]),
            {"review_id", "order_id", "score", "title", "text", "created_at", "order_date", "categories", "retrieval_score"},
        )
        self.assertNotIn("id", results[0])
        fallback = self.store.search("excelen")
        self.assertEqual([item["review_id"] for item in fallback], ["r2"])

    def test_empty_text_is_counted_but_not_indexed_for_search(self) -> None:
        stats = self.store.stats({"score": 2})
        self.assertEqual(stats["total_reviews"], 1)
        self.assertEqual(stats["text_reviews"], 0)
        self.store.ensure_ready()
        self.assertEqual(
            self.store.search(["produto", "entrega", "excelente", "quality"], {"score": 2}),
            [],
        )
        with closing(sqlite3.connect(self.index_path)) as conn:
            self.assertEqual(
                conn.execute("SELECT has_text FROM reviews WHERE review_id = 'r3'").fetchone()[0],
                0,
            )

    def test_source_change_triggers_atomic_rebuild(self) -> None:
        self.assertEqual(self.store.stats()["total_reviews"], 5)
        review_path = self.raw_dir / "olist_order_reviews_dataset.csv"
        with review_path.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["r6", "o4", 1, "Nova", "Avaliação muito ruim", "2018-05-01 00:00:00"])

        stats = self.store.stats()
        self.assertEqual(stats["total_reviews"], 6)
        self.assertEqual(stats["low_score_reviews"], 3)
        self.assertEqual(stats["low_score_ratio"], 50.0)
        self.assertEqual(self.store.search("Avaliação")[0]["review_id"], "r6")
        for name in (
            "olist_order_reviews_dataset.csv",
            "olist_orders_dataset.csv",
            "olist_order_items_dataset.csv",
            "olist_products_dataset.csv",
            "product_category_name_translation.csv",
        ):
            self.assertTrue((self.raw_dir / name).is_file())

    def test_invalid_filters_and_limits_are_rejected(self) -> None:
        invalid_filters = [
            {"unknown": 1},
            {"month": 13},
            {"score": 0},
            {"start_date": "2017-02-30"},
            {"category": ""},
            {"min_score": 4, "max_score": 2},
        ]
        for filters in invalid_filters:
            with self.subTest(filters=filters), self.assertRaises(ValueError):
                self.store.stats(filters)
        self.assertEqual(self.store.stats({"category": "health_beauty\" OR 1=1 --"})["total_reviews"], 0)
        for limit in (0, 21, True):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                self.store.search("produto", limit=limit)


if __name__ == "__main__":
    unittest.main()
