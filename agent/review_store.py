# -*- coding: utf-8 -*-
"""基于 SQLite FTS5 的本地评论语义检索与统计组件。"""
from __future__ import annotations

import csv
import json
import os
import re
import sqlite3
import tempfile
import threading
from collections.abc import Mapping
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INDEX_PATH = ROOT / "data" / "review_index" / "reviews.sqlite3"
SOURCE_FILES = (
    "olist_order_reviews_dataset.csv",
    "olist_orders_dataset.csv",
    "olist_order_items_dataset.csv",
    "olist_products_dataset.csv",
    "product_category_name_translation.csv",
)
_FILTER_NAMES = (
    "min_score",
    "max_score",
    "score",
    "year",
    "month",
    "start_date",
    "end_date",
    "category",
)
_FTS_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_RRF_K = 60


class ReviewIndexError(RuntimeError):
    """评论索引缺失、损坏或重建失败时抛出的明确异常。"""


class ReviewStore:
    """评论索引入口。

    索引只在首次使用时构建；五个源 CSV 的路径、大小和纳秒修改时间共同组成
    签名，签名变化后自动重建。重建在同级临时文件完成，再原子替换旧库。
    时间过滤均作用于评论创建时间 ``created_at``，起止日期均包含当天。
    """

    def __init__(
        self,
        index_path: str | os.PathLike[str] | None = None,
        raw_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        """创建惰性索引对象，可分别覆盖索引位置和原始数据目录。"""
        if raw_dir is None:
            from datamind_config import load_dataset_config

            raw_dir = load_dataset_config()["raw_dir"]
        configured_path = index_path or os.getenv("DATAMIND_REVIEW_INDEX_PATH")
        self.index_path = Path(configured_path or DEFAULT_INDEX_PATH).expanduser().resolve()
        self.raw_dir = Path(raw_dir).expanduser().resolve()
        self._lock = threading.RLock()

    def ensure_ready(self) -> None:
        """确保索引可用；源文件变化时重建，失败则保留旧库并抛出异常。"""
        with self._lock:
            signature = self._source_signature()
            if self._is_current(signature):
                return
            self._rebuild(signature)

    def status(self) -> dict:
        """返回索引状态，仅包含就绪状态、文档数量和构建时间等元信息。"""
        with self._lock:
            self.ensure_ready()
            with closing(self._connect()) as conn:
                counts = conn.execute(
                    "SELECT COUNT(*) AS total, COALESCE(SUM(has_text), 0) AS texts "
                    "FROM reviews"
                ).fetchone()
                row = conn.execute(
                    "SELECT value FROM index_metadata WHERE key = 'indexed_at'"
                ).fetchone()
            return {
                "ready": True,
                "document_count": int(counts["total"]),
                "text_document_count": int(counts["texts"]),
                "indexed_at": row["value"] if row else None,
            }

    def stats(self, filters: Mapping | None = None) -> dict:
        """用参数化 SQL 汇总评论，支持白名单分数、时间和品类过滤。

        ``low_score_ratio`` 是低分评论占全部过滤后评论的百分数，保留两位小数；
        ``categories`` 在库中保存为 JSON，但本接口的过滤条件按数组成员精确匹配。
        """
        applied = self._normalize_filters(filters)
        conditions, params = self._filter_conditions(applied)
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._lock:
            self.ensure_ready()
            with closing(self._connect()) as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS total, "
                    "COALESCE(SUM(r.has_text), 0) AS texts, "
                    "AVG(r.score) AS average_score, "
                    "COALESCE(SUM(CASE WHEN r.score <= 2 THEN 1 ELSE 0 END), 0) AS low, "
                    "COALESCE(SUM(CASE WHEN r.score >= 4 THEN 1 ELSE 0 END), 0) AS positive, "
                    "MIN(substr(r.created_at, 1, 10)) AS date_start, "
                    "MAX(substr(r.created_at, 1, 10)) AS date_end "
                    f"FROM reviews r{where}",
                    params,
                ).fetchone()
                distribution_rows = conn.execute(
                    f"SELECT r.score, COUNT(*) AS count FROM reviews r{where} "
                    "GROUP BY r.score ORDER BY r.score",
                    params,
                ).fetchall()

        total = int(row["total"])
        low = int(row["low"])
        average = row["average_score"]
        return {
            "total_reviews": total,
            "text_reviews": int(row["texts"]),
            "average_score": round(float(average), 2) if average is not None else None,
            "low_score_reviews": low,
            "low_score_ratio": round(low * 100.0 / total, 2) if total else 0.0,
            "positive_score_reviews": int(row["positive"]),
            "score_distribution": [
                {"score": int(item["score"]), "count": int(item["count"])}
                for item in distribution_rows
            ],
            "date_start": row["date_start"],
            "date_end": row["date_end"],
            "applied_filters": applied,
        }

    def search(
        self,
        queries: str | list[str],
        filters: Mapping | None = None,
        limit: int = 8,
    ) -> list[dict]:
        """安全检索评论原文，并用 reciprocal rank fusion 合并多查询结果。

        每个查询先转换为带引号的 FTS5 字面词项，分别按 BM25 排序，再以 RRF
        合并；不会把用户输入当作 FTS 语法执行。无 FTS 命中时，以少量转义后的
        参数化 LIKE 关键词兜底。最终结果对规范化正文去重，并优先覆盖不同品类。
        """
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
            raise ValueError("limit 必须是 1 到 20 之间的整数")
        query_list = self._normalize_queries(queries)
        applied = self._normalize_filters(filters)
        filter_parts, filter_params = self._filter_conditions(applied)
        if not query_list:
            return []

        with self._lock:
            self.ensure_ready()
            with closing(self._connect()) as conn:
                candidates: dict[int, dict] = {}
                candidate_limit = min(200, max(40, limit * 8))
                for query in query_list:
                    match = self._fts_match(query)
                    if not match:
                        continue
                    conditions = ["reviews_fts MATCH ?", *filter_parts]
                    rows = conn.execute(
                        "SELECT r.id, r.review_id, r.order_id, r.score, r.title, r.body, "
                        "r.created_at, r.order_date, r.categories, "
                        "bm25(reviews_fts, 0.4, 1.0) AS bm25_score "
                        "FROM reviews_fts JOIN reviews r ON r.id = reviews_fts.rowid "
                        f"WHERE {' AND '.join(conditions)} "
                        "ORDER BY bm25_score ASC, r.id ASC LIMIT ?",
                        [match, *filter_params, candidate_limit],
                    ).fetchall()
                    for rank, row in enumerate(rows, 1):
                        entry = candidates.setdefault(
                            int(row["id"]),
                            {"row": row, "score": 0.0, "best_rank": rank},
                        )
                        entry["score"] += 1.0 / (_RRF_K + rank)
                        entry["best_rank"] = min(entry["best_rank"], rank)

                if candidates:
                    ordered = sorted(
                        candidates.values(),
                        key=lambda item: (-item["score"], item["best_rank"], item["row"]["id"]),
                    )
                else:
                    ordered = self._like_fallback(conn, query_list, filter_parts, filter_params)
                return self._diverse_results(ordered, limit)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.index_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        return conn

    def _source_signature(self) -> list[dict]:
        signature = []
        for name in SOURCE_FILES:
            path = self.raw_dir / name
            try:
                stat = path.stat()
            except OSError as exc:
                raise ReviewIndexError(f"无法读取评论源文件：{path}；{exc}") from exc
            if not path.is_file():
                raise ReviewIndexError(f"评论源路径不是文件：{path}")
            signature.append(
                {"path": str(path), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
            )
        return signature

    @staticmethod
    def _signature_text(signature: list[dict]) -> str:
        return json.dumps(signature, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _is_current(self, signature: list[dict]) -> bool:
        if not self.index_path.is_file():
            return False
        try:
            with closing(self._connect()) as conn:
                row = conn.execute(
                    "SELECT value FROM index_metadata WHERE key = 'source_signature'"
                ).fetchone()
                if not row or row["value"] != self._signature_text(signature):
                    return False
                conn.execute("SELECT id, categories FROM reviews LIMIT 1").fetchone()
                conn.execute("SELECT rowid FROM reviews_fts LIMIT 1").fetchone()
            return True
        except (OSError, sqlite3.Error):
            return False

    def _rebuild(self, signature: list[dict]) -> None:
        temp_path: Path | None = None
        conn: sqlite3.Connection | None = None
        try:
            self.index_path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(
                prefix=f".{self.index_path.name}.", suffix=".tmp", dir=self.index_path.parent
            )
            os.close(fd)
            temp_path = Path(temp_name)
            conn = sqlite3.connect(str(temp_path))
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode = OFF")
            conn.execute("PRAGMA synchronous = OFF")
            conn.execute("PRAGMA temp_store = MEMORY")
            self._create_schema(conn)
            self._populate(conn)
            indexed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            conn.executemany(
                "INSERT INTO index_metadata(key, value) VALUES (?, ?)",
                (
                    ("source_signature", self._signature_text(signature)),
                    ("indexed_at", indexed_at),
                ),
            )
            conn.commit()
            check = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if check != "ok":
                raise sqlite3.DatabaseError(f"临时索引完整性检查失败：{check}")
            conn.close()
            conn = None
            os.replace(temp_path, self.index_path)
            temp_path = None
        except Exception as exc:
            raise ReviewIndexError(f"评论索引重建失败，现有索引未被修改：{exc}") from exc
        finally:
            if conn is not None:
                conn.close()
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def _create_schema(conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE reviews (
                id INTEGER PRIMARY KEY,
                review_id TEXT NOT NULL,
                order_id TEXT NOT NULL,
                score INTEGER NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                body TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                order_date TEXT NOT NULL DEFAULT '',
                categories TEXT NOT NULL DEFAULT '[]',
                has_text INTEGER NOT NULL DEFAULT 0 CHECK (has_text IN (0, 1))
            );
            CREATE VIRTUAL TABLE reviews_fts USING fts5(
                title,
                body,
                content='reviews',
                content_rowid='id',
                tokenize='unicode61'
            );
            CREATE TABLE index_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )

    def _populate(self, conn: sqlite3.Connection) -> None:
        translations: dict[str, str] = {}
        with (self.raw_dir / "product_category_name_translation.csv").open(
            "r", encoding="utf-8-sig", newline=""
        ) as handle:
            for row in csv.DictReader(handle):
                source = (row.get("product_category_name") or "").strip()
                english = (row.get("product_category_name_english") or "").strip()
                if source and english:
                    translations[source] = english

        product_categories: dict[str, str] = {}
        with (self.raw_dir / "olist_products_dataset.csv").open(
            "r", encoding="utf-8-sig", newline=""
        ) as handle:
            for row in csv.DictReader(handle):
                product_id = row.get("product_id") or ""
                source = (row.get("product_category_name") or "").strip()
                category = translations.get(source, source)
                if product_id and category:
                    product_categories[product_id] = category

        order_categories: dict[str, set[str]] = {}
        with (self.raw_dir / "olist_order_items_dataset.csv").open(
            "r", encoding="utf-8-sig", newline=""
        ) as handle:
            for row in csv.DictReader(handle):
                order_id = row.get("order_id") or ""
                category = product_categories.get(row.get("product_id") or "", "")
                if order_id and category:
                    order_categories.setdefault(order_id, set()).add(category)

        order_dates: dict[str, str] = {}
        with (self.raw_dir / "olist_orders_dataset.csv").open(
            "r", encoding="utf-8-sig", newline=""
        ) as handle:
            for row in csv.DictReader(handle):
                order_id = row.get("order_id") or ""
                if order_id:
                    order_dates[order_id] = row.get("order_purchase_timestamp") or ""

        insert_sql = (
            "INSERT INTO reviews(review_id, order_id, score, title, body, created_at, "
            "order_date, categories, has_text) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        batch: list[tuple] = []
        with (self.raw_dir / "olist_order_reviews_dataset.csv").open(
            "r", encoding="utf-8-sig", newline=""
        ) as handle:
            for row in csv.DictReader(handle):
                order_id = row.get("order_id") or ""
                title = row.get("review_comment_title") or ""
                body = row.get("review_comment_message") or ""
                categories = sorted(order_categories.get(order_id, set()))
                batch.append(
                    (
                        row.get("review_id") or "",
                        order_id,
                        int(row["review_score"]),
                        title,
                        body,
                        row.get("review_creation_date") or "",
                        order_dates.get(order_id, ""),
                        json.dumps(categories, ensure_ascii=False, separators=(",", ":")),
                        int(bool(title.strip() or body.strip())),
                    )
                )
                if len(batch) >= 1000:
                    conn.executemany(insert_sql, batch)
                    batch.clear()
        if batch:
            conn.executemany(insert_sql, batch)

        conn.execute(
            "INSERT INTO reviews_fts(rowid, title, body) "
            "SELECT id, title, body FROM reviews WHERE has_text = 1"
        )
        conn.executescript(
            """
            CREATE INDEX idx_reviews_score ON reviews(score);
            CREATE INDEX idx_reviews_created_at ON reviews(created_at);
            CREATE INDEX idx_reviews_order_date ON reviews(order_date);
            CREATE INDEX idx_reviews_order_id ON reviews(order_id);
            CREATE INDEX idx_reviews_has_text ON reviews(has_text);
            """
        )

    @classmethod
    def _normalize_filters(cls, filters: Mapping | None) -> dict:
        if filters is None:
            return {}
        if not isinstance(filters, Mapping):
            raise TypeError("filters 必须是字典或 None")
        unknown = set(filters) - set(_FILTER_NAMES)
        if unknown:
            names = ", ".join(sorted((str(key) for key in unknown)))
            raise ValueError(f"不支持的评论过滤条件：{names}")

        applied: dict = {}
        for name in ("min_score", "max_score", "score"):
            if name in filters:
                applied[name] = cls._integer_filter(name, filters[name], 1, 5)
        if "year" in filters:
            applied["year"] = cls._integer_filter("year", filters["year"], 1000, 9999)
        if "month" in filters:
            applied["month"] = cls._integer_filter("month", filters["month"], 1, 12)
        for name in ("start_date", "end_date"):
            if name in filters:
                value = filters[name]
                if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
                    raise ValueError(f"{name} 必须使用 YYYY-MM-DD 格式")
                try:
                    date.fromisoformat(value)
                except ValueError as exc:
                    raise ValueError(f"{name} 不是有效日期：{value}") from exc
                applied[name] = value
        if "category" in filters:
            category = filters["category"]
            if not isinstance(category, str) or not category.strip():
                raise ValueError("category 必须是非空字符串")
            applied["category"] = category.strip()

        if applied.get("min_score", 1) > applied.get("max_score", 5):
            raise ValueError("min_score 不能大于 max_score")
        if "start_date" in applied and "end_date" in applied:
            if applied["start_date"] > applied["end_date"]:
                raise ValueError("start_date 不能晚于 end_date")
        return applied

    @staticmethod
    def _integer_filter(name: str, value: object, minimum: int, maximum: int) -> int:
        if isinstance(value, bool):
            raise ValueError(f"{name} 必须是整数")
        if isinstance(value, int):
            number = value
        elif isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
            number = int(value.strip())
        else:
            raise ValueError(f"{name} 必须是整数")
        if not minimum <= number <= maximum:
            raise ValueError(f"{name} 必须在 {minimum} 到 {maximum} 之间")
        return number

    @staticmethod
    def _filter_conditions(filters: dict) -> tuple[list[str], list]:
        conditions: list[str] = []
        params: list = []
        if "min_score" in filters:
            conditions.append("r.score >= ?")
            params.append(filters["min_score"])
        if "max_score" in filters:
            conditions.append("r.score <= ?")
            params.append(filters["max_score"])
        if "score" in filters:
            conditions.append("r.score = ?")
            params.append(filters["score"])
        if "year" in filters:
            conditions.append("substr(r.created_at, 1, 4) = ?")
            params.append(f"{filters['year']:04d}")
        if "month" in filters:
            conditions.append("substr(r.created_at, 6, 2) = ?")
            params.append(f"{filters['month']:02d}")
        if "start_date" in filters:
            conditions.append("substr(r.created_at, 1, 10) >= ?")
            params.append(filters["start_date"])
        if "end_date" in filters:
            conditions.append("substr(r.created_at, 1, 10) <= ?")
            params.append(filters["end_date"])
        if "category" in filters:
            # JSON 字符串包含引号，子串命中即表示完整数组成员相等，而非前缀相等。
            conditions.append("instr(r.categories, ?) > 0")
            params.append(json.dumps(filters["category"], ensure_ascii=False))
        return conditions, params

    @staticmethod
    def _normalize_queries(queries: str | list[str]) -> list[str]:
        if isinstance(queries, str):
            values = [queries]
        elif isinstance(queries, (list, tuple)):
            values = queries
        else:
            raise TypeError("queries 必须是字符串或字符串列表")
        if not all(isinstance(value, str) for value in values):
            raise TypeError("queries 中的每个查询都必须是字符串")
        result = []
        seen = set()
        for value in values:
            query = value.strip()
            if query and query not in seen:
                result.append(query)
                seen.add(query)
        return result

    @staticmethod
    def _terms(query: str) -> list[str]:
        return _FTS_TOKEN_RE.findall(query)

    @classmethod
    def _fts_match(cls, query: str) -> str:
        terms = cls._terms(query)[:16]
        return " OR ".join(f'"{term}"' for term in terms)

    @staticmethod
    def _escape_like(value: str) -> str:
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    def _like_fallback(
        self,
        conn: sqlite3.Connection,
        queries: list[str],
        filter_parts: list[str],
        filter_params: list,
    ) -> list[dict]:
        terms = []
        seen = set()
        for query in queries:
            for term in self._terms(query):
                folded = term.casefold()
                if folded not in seen:
                    terms.append(term)
                    seen.add(folded)
                if len(terms) >= 8:
                    break
            if len(terms) >= 8:
                break
        if not terms:
            return []

        conditions = [*filter_parts, "r.has_text = 1"]
        params = [*filter_params]
        keyword_conditions = []
        for term in terms:
            keyword_conditions.append("(r.title LIKE ? ESCAPE '\\' OR r.body LIKE ? ESCAPE '\\')")
            pattern = f"%{self._escape_like(term)}%"
            params.extend((pattern, pattern))
        conditions.append(f"({' OR '.join(keyword_conditions)})")
        rows = conn.execute(
            "SELECT r.id, r.review_id, r.order_id, r.score, r.title, r.body, "
            "r.created_at, r.order_date, r.categories "
            f"FROM reviews r WHERE {' AND '.join(conditions)} LIMIT ?",
            [*params, 200],
        ).fetchall()
        entries = []
        for row in rows:
            haystack = f"{row['title']}\n{row['body']}".casefold()
            matches = sum(1 for term in terms if term.casefold() in haystack)
            entries.append({"row": row, "matches": matches, "score": 0.0, "best_rank": 0})
        entries.sort(key=lambda item: (-item["matches"], item["row"]["id"]))
        for rank, entry in enumerate(entries, 1):
            entry["score"] = 1.0 / (_RRF_K + rank)
            entry["best_rank"] = rank
        return entries

    @staticmethod
    def _normalize_text(value: str) -> str:
        return " ".join(value.casefold().split())

    @classmethod
    def _diverse_results(cls, entries: list[dict], limit: int) -> list[dict]:
        unique = []
        seen_text = set()
        for entry in entries:
            row = entry["row"]
            normalized = cls._normalize_text(row["body"] or row["title"])
            if normalized and normalized in seen_text:
                continue
            if normalized:
                seen_text.add(normalized)
            categories = json.loads(row["categories"])
            unique.append(
                {
                    "review_id": row["review_id"],
                    "order_id": row["order_id"],
                    "score": int(row["score"]),
                    "title": row["title"],
                    "text": row["body"],
                    "created_at": row["created_at"],
                    "order_date": row["order_date"],
                    "categories": categories,
                    "retrieval_score": round(float(entry["score"]), 6),
                    "_category_key": tuple(categories),
                }
            )

        selected = unique[:0]
        deferred = []
        used_categories = set()
        for item in unique:
            key = item["_category_key"]
            if len(selected) < limit and key not in used_categories:
                selected.append(item)
                used_categories.add(key)
            else:
                deferred.append(item)
        selected.extend(deferred[: max(0, limit - len(selected))])
        for item in selected:
            item.pop("_category_key", None)
        return selected[:limit]


_singleton: ReviewStore | None = None
_singleton_lock = threading.Lock()


def get_review_store() -> ReviewStore:
    """返回进程级默认 ReviewStore 单例，环境变量可覆盖其索引路径。"""
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = ReviewStore()
    return _singleton
