"""SQLite knowledge base.

Retrieval is BM25 over SQLite's FTS5. That is a deliberate choice: it needs no
embedding model, no external service and no network, so the whole knowledge base
is a single file the researcher owns and can back up (QA-3, data stays local).
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .pubmed import Article

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    pmid            TEXT PRIMARY KEY,
    title           TEXT NOT NULL DEFAULT '',
    abstract        TEXT NOT NULL DEFAULT '',
    journal         TEXT NOT NULL DEFAULT '',
    journal_abbrev  TEXT NOT NULL DEFAULT '',
    year            TEXT NOT NULL DEFAULT '',
    authors         TEXT NOT NULL DEFAULT '[]',
    doi             TEXT NOT NULL DEFAULT '',
    publication_types TEXT NOT NULL DEFAULT '[]',
    mesh_terms      TEXT NOT NULL DEFAULT '[]',
    topic           TEXT NOT NULL DEFAULT '',
    added_at        TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
    pmid UNINDEXED,
    title,
    abstract,
    mesh_terms,
    tokenize = 'unicode61'
);

-- Structured extraction output, one card per article.
CREATE TABLE IF NOT EXISTS cards (
    pmid        TEXT PRIMARY KEY REFERENCES articles(pmid),
    payload     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

-- Hypotheses are append-only so earlier versions stay comparable (Clause 2.4).
CREATE TABLE IF NOT EXISTS hypotheses (
    version     INTEGER PRIMARY KEY AUTOINCREMENT,
    payload     TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS journals (
    name        TEXT PRIMARY KEY,
    payload     TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_articles_year ON articles(year);
CREATE INDEX IF NOT EXISTS idx_articles_topic ON articles(topic);
"""

# FTS5 treats these as query syntax; strip them out of free-text queries.
_FTS_TOKEN = re.compile(r"[A-Za-z0-9一-鿿][A-Za-z0-9一-鿿\-]*")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------- articles

    def add_articles(self, articles: Iterable[Article], topic: str = "") -> int:
        """Insert articles, skipping ones already stored. Returns the new count."""
        added = 0
        for article in articles:
            if self.has_article(article.pmid):
                continue
            self.conn.execute(
                """INSERT INTO articles
                   (pmid, title, abstract, journal, journal_abbrev, year, authors,
                    doi, publication_types, mesh_terms, topic, added_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    article.pmid,
                    article.title,
                    article.abstract,
                    article.journal,
                    article.journal_abbrev,
                    article.year,
                    json.dumps(article.authors, ensure_ascii=False),
                    article.doi,
                    json.dumps(article.publication_types, ensure_ascii=False),
                    json.dumps(article.mesh_terms, ensure_ascii=False),
                    topic,
                    _now(),
                ),
            )
            self.conn.execute(
                "INSERT INTO articles_fts (pmid, title, abstract, mesh_terms) VALUES (?,?,?,?)",
                (article.pmid, article.title, article.abstract, " ; ".join(article.mesh_terms)),
            )
            added += 1
        self.conn.commit()
        return added

    def has_article(self, pmid: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM articles WHERE pmid = ?", (pmid,)).fetchone()
        return row is not None

    def get_article(self, pmid: str) -> Article | None:
        row = self.conn.execute("SELECT * FROM articles WHERE pmid = ?", (pmid,)).fetchone()
        return _row_to_article(row) if row else None

    def all_pmids(self) -> list[str]:
        return [r["pmid"] for r in self.conn.execute("SELECT pmid FROM articles ORDER BY pmid")]

    def count_articles(self) -> int:
        return self.conn.execute("SELECT COUNT(*) AS n FROM articles").fetchone()["n"]

    def search(self, query: str, limit: int = 12) -> list[tuple[Article, float]]:
        """BM25 retrieval. Lower bm25() is a better match; we return it negated
        so callers can sort descending on 'score' like any other relevance value."""
        match = to_fts_query(query)
        if not match:
            return []
        rows = self.conn.execute(
            """SELECT pmid, bm25(articles_fts, 3.0, 1.0, 1.5) AS rank
               FROM articles_fts WHERE articles_fts MATCH ?
               ORDER BY rank LIMIT ?""",
            (match, limit),
        ).fetchall()

        results = []
        for row in rows:
            article = self.get_article(row["pmid"])
            if article:
                results.append((article, -float(row["rank"])))
        return results

    # ---------------------------------------------------------------- cards

    def save_card(self, pmid: str, payload: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO cards (pmid, payload, created_at) VALUES (?,?,?)",
            (pmid, json.dumps(payload, ensure_ascii=False), _now()),
        )
        self.conn.commit()

    def get_card(self, pmid: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT payload FROM cards WHERE pmid = ?", (pmid,)).fetchone()
        return json.loads(row["payload"]) if row else None

    def pmids_without_cards(self) -> list[str]:
        rows = self.conn.execute(
            """SELECT a.pmid FROM articles a
               LEFT JOIN cards c ON c.pmid = a.pmid
               WHERE c.pmid IS NULL AND TRIM(a.abstract) != ''
               ORDER BY a.year DESC"""
        )
        return [r["pmid"] for r in rows]

    def count_cards(self) -> int:
        return self.conn.execute("SELECT COUNT(*) AS n FROM cards").fetchone()["n"]

    # ----------------------------------------------------------- hypotheses

    def save_hypothesis(self, payload: dict[str, Any], note: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO hypotheses (payload, note, created_at) VALUES (?,?,?)",
            (json.dumps(payload, ensure_ascii=False), note, _now()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def latest_hypothesis(self) -> tuple[int, dict[str, Any]] | None:
        row = self.conn.execute(
            "SELECT version, payload FROM hypotheses ORDER BY version DESC LIMIT 1"
        ).fetchone()
        return (row["version"], json.loads(row["payload"])) if row else None

    def get_hypothesis(self, version: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT payload FROM hypotheses WHERE version = ?", (version,)
        ).fetchone()
        return json.loads(row["payload"]) if row else None

    def list_hypotheses(self) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT version, note, created_at FROM hypotheses ORDER BY version"
            )
        )

    # -------------------------------------------------------------- journals

    def save_journal(self, name: str, payload: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO journals (name, payload, updated_at) VALUES (?,?,?)",
            (name.lower(), json.dumps(payload, ensure_ascii=False), _now()),
        )
        self.conn.commit()

    def get_journal(self, name: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT payload FROM journals WHERE name = ?", (name.lower(),)
        ).fetchone()
        return json.loads(row["payload"]) if row else None

    def list_journals(self) -> list[str]:
        return [r["name"] for r in self.conn.execute("SELECT name FROM journals ORDER BY name")]

    # ------------------------------------------------------------------ chat

    def append_chat(self, role: str, content: str) -> None:
        self.conn.execute(
            "INSERT INTO chat (role, content, created_at) VALUES (?,?,?)",
            (role, content, _now()),
        )
        self.conn.commit()

    def chat_history(self, limit: int = 40) -> list[dict[str, str]]:
        rows = self.conn.execute(
            "SELECT role, content FROM chat ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    def clear_chat(self) -> None:
        self.conn.execute("DELETE FROM chat")
        self.conn.commit()


def to_fts_query(text: str) -> str:
    """Turn free text into a safe FTS5 MATCH expression.

    Every token is quoted so that punctuation, hyphens and operators in a
    clinical phrase ("NF-kB", "TGF-beta/Smad") cannot be read as query syntax.
    """
    tokens = [t for t in _FTS_TOKEN.findall(text) if len(t) > 1]
    if not tokens:
        return ""
    return " OR ".join(f'"{t}"' for t in tokens)


def _row_to_article(row: sqlite3.Row) -> Article:
    return Article(
        pmid=row["pmid"],
        title=row["title"],
        abstract=row["abstract"],
        journal=row["journal"],
        journal_abbrev=row["journal_abbrev"],
        year=row["year"],
        authors=json.loads(row["authors"]),
        doi=row["doi"],
        publication_types=json.loads(row["publication_types"]),
        mesh_terms=json.loads(row["mesh_terms"]),
    )


def article_to_dict(article: Article) -> dict[str, Any]:
    return asdict(article)


__all__ = ["Store", "to_fts_query", "article_to_dict"]


def open_store(path: Path) -> Store:
    with closing(sqlite3.connect(path)) as probe:
        probe.execute("PRAGMA journal_mode=WAL")
    return Store(path)
