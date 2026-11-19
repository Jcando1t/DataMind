# -*- coding: utf-8 -*-
"""评论意图、混合分析节点与响应引用的离线测试。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.nodes.intent import intent_node, is_review_question
from agent.nodes.review_insight import (
    _expand_queries,
    _extract_json_list,
    _parse_filters,
    review_insight_node,
)
from agent.review_store import ReviewIndexError


def _stats(total=10, texts=8, low=3):
    return {
        "total_reviews": total, "text_reviews": texts, "average_score": 3.7,
        "low_score_reviews": low, "low_score_ratio": round(low * 100 / total, 2) if total else 0,
        "positive_score_reviews": 4,
        "score_distribution": [{"score": score, "count": 2} for score in range(1, 6)],
        "date_start": "2017-01-01", "date_end": "2018-01-01", "applied_filters": {},
    }


class FakeStore:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.stats_calls = []
        self.search_calls = []

    def ensure_ready(self):
        return None

    def stats(self, filters=None):
        self.stats_calls.append(filters)
        if filters and ("max_score" in filters or "score" in filters):
            return _stats(total=3, texts=3, low=3)
        return _stats()

    def search(self, queries, filters=None, limit=8):
        self.search_calls.append((queries, filters, limit))
        return self.rows[:limit]

    def status(self):
        return {"ready": True, "document_count": 10, "text_document_count": 8, "indexed_at": "2026-11-19T00:00:00+00:00"}


def _row(review_id="review-1", order_id="order-1"):
    return {
        "review_id": review_id, "order_id": order_id, "score": 1,
        "title": "Entrega ruim", "text": "Produto chegou quebrado e entrega atrasada.",
        "created_at": "2017-01-10 00:00:00", "order_date": "2017-01-02 08:00:00",
        "categories": ["health_beauty"], "retrieval_score": 0.02,
    }


class IntentTests(unittest.TestCase):
    def test_obvious_review_question_does_not_call_classifier(self):
        with patch("agent.nodes.intent.chat", side_effect=AssertionError("不应调用分类模型")):
            result = intent_node({"user_question": "低分评论主要抱怨什么？", "chat_history": []})
        self.assertEqual(result["intent"], "review_query")
        self.assertTrue(is_review_question("有哪些物流差评？"))

    def test_short_followup_inherits_review_intent(self):
        history = [{"role": "user", "content": "分析低分评论"}, {"role": "assistant", "content": "评价统计"}]
        self.assertTrue(is_review_question("那物流方面呢？", history))
        self.assertFalse(is_review_question("GMV按月趋势怎么样？", history))


class FilterAndExpansionTests(unittest.TestCase):
    def test_filters_separate_scope_from_sentiment(self):
        scope, retrieval = _parse_filters("2017年健康美容品类的低分评论在抱怨什么")
        self.assertEqual(scope, {"year": 2017, "category": "health_beauty"})
        self.assertEqual(retrieval, {"max_score": 2, "year": 2017, "category": "health_beauty"})
        self.assertEqual(_parse_filters("找出1分评论")[1]["score"], 1)
        self.assertEqual(_parse_filters("2018-03的好评")[1], {"min_score": 4, "year": 2018, "month": 3})

    def test_json_expansion_is_strict_and_has_fallback(self):
        self.assertEqual(_extract_json_list('{"queries":["entrega atrasada"]}'), ["entrega atrasada"])
        self.assertEqual(_extract_json_list('```json\n{"queries":["produto quebrado"]}\n```'), ["produto quebrado"])
        self.assertEqual(_extract_json_list("not json"), [])
        with patch("agent.nodes.review_insight.chat", return_value='{"queries":["entrega atrasada"]}'):
            queries, mode = _expand_queries("物流低分评论")
        self.assertEqual(mode, "llm_expansion")
        self.assertIn("entrega atrasada", queries)
        self.assertTrue(any("correios" in query for query in queries))


class ReviewNodeTests(unittest.TestCase):
    def test_stats_retrieval_citations_and_grounded_answer(self):
        store = FakeStore([_row()])
        state = {"user_question": "低分评论主要抱怨什么？", "chat_history": []}
        with patch("agent.nodes.review_insight.get_review_store", return_value=store), \
             patch("agent.nodes.review_insight.chat", side_effect=[
                 '{"queries":["produto quebrado","entrega atrasada"]}',
                 "- 商品破损与配送延迟是主要问题。[R1]",
             ]):
            result = review_insight_node(state)
        self.assertIsNone(result["error_category"])
        self.assertEqual(result["response_type"], "review_insight")
        self.assertEqual(store.stats_calls[0], {})
        self.assertEqual(store.stats_calls[1], {"max_score": 2})
        self.assertEqual(store.search_calls[0][1], {"max_score": 2})
        insight = result["review_insight"]
        self.assertEqual(insight["stats"]["total_reviews"], 10)
        self.assertEqual(insight["matching_stats"]["total_reviews"], 3)
        self.assertEqual(insight["citations"][0]["citation_id"], "R1")
        self.assertEqual(insight["citations"][0]["text"], _row()["text"])
        self.assertIn("平均评分 3.70 分", result["final_answer"])
        self.assertIn("[R1]", result["final_answer"])
        self.assertNotIn("[R2]", result["final_answer"])

    def test_invalid_model_citation_is_removed(self):
        store = FakeStore([_row()])
        with patch("agent.nodes.review_insight.get_review_store", return_value=store), \
             patch("agent.nodes.review_insight.chat", side_effect=[
                 '{"queries":["produto quebrado"]}', "- 破损问题。[R99]",
             ]):
            result = review_insight_node({"user_question": "低分评论", "chat_history": []})
        self.assertNotIn("R99", result["final_answer"])

    def test_no_matching_text_does_not_call_generation(self):
        store = FakeStore([])
        with patch("agent.nodes.review_insight.get_review_store", return_value=store), \
             patch("agent.nodes.review_insight.chat", return_value='{"queries":["sem correspondencia"]}'):
            result = review_insight_node({"user_question": "低分评论", "chat_history": []})
        self.assertEqual(result["review_insight"]["retrieval"]["summary_mode"], "no_matching_text")
        self.assertIn("未检索到", result["final_answer"])

    def test_index_failure_has_explicit_business_category(self):
        with patch("agent.nodes.review_insight.get_review_store", side_effect=ReviewIndexError("synthetic")):
            result = review_insight_node({"user_question": "低分评论", "chat_history": []})
        self.assertEqual(result["error_category"], "review_index")
        self.assertIsNone(result["review_insight"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
