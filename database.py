"""
Shared SQLite database layer for all three CNA News apps.
"""
import sqlite3
import json
import logging
from contextlib import contextmanager
from typing import Optional, List, Dict, Any

DB_PATH = "news.db"

logger = logging.getLogger(__name__)


@contextmanager
def get_conn():
    """Yield a SQLite connection with WAL mode and row_factory set."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create tables and indexes if they don't exist."""
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS articles (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                url          TEXT    UNIQUE NOT NULL,
                title        TEXT    NOT NULL,
                section      TEXT    NOT NULL,
                summary      TEXT,
                full_text    TEXT,
                published_at TEXT,
                scraped_at   TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_articles_section
                ON articles(section);
            CREATE INDEX IF NOT EXISTS idx_articles_scraped_at
                ON articles(scraped_at);

            CREATE TABLE IF NOT EXISTS chat_history (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id   TEXT    NOT NULL,
                role         TEXT    NOT NULL CHECK(role IN ('user','assistant')),
                content      TEXT    NOT NULL,
                article_ids  TEXT,
                created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_chat_session
                ON chat_history(session_id);
            CREATE INDEX IF NOT EXISTS idx_chat_created
                ON chat_history(created_at);
        """)


def article_exists(url: str) -> bool:
    """Return True if an article with this URL is already in the DB."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM articles WHERE url = ?", (url,)
        ).fetchone()
        return row is not None


def insert_article(
    url: str,
    title: str,
    section: str,
    full_text: str,
    published_at: Optional[str] = None,
    summary: Optional[str] = None,
) -> int:
    """Insert a new article; return its rowid. Raises on duplicate url."""
    with get_conn() as conn:
        cursor = conn.execute(
            """INSERT INTO articles (url, title, section, full_text, published_at, summary)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (url, title, section, full_text, published_at, summary),
        )
        return cursor.lastrowid


def update_summary(article_id: int, summary: str) -> None:
    """Set the summary field for an article."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE articles SET summary = ? WHERE id = ?",
            (summary, article_id),
        )


def get_articles(
    section: Optional[str] = None,
    days: int = 1,
    limit: int = 100,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """Return articles from the last `days` days, newest first."""
    with get_conn() as conn:
        params: list = [days]
        section_clause = ""
        if section and section != "all":
            section_clause = "AND section = ?"
            params.append(section)
        params += [limit, offset]
        rows = conn.execute(
            f"""SELECT id, url, title, section, summary, published_at, scraped_at
                FROM articles
                WHERE scraped_at >= datetime('now', ? || ' days')
                {section_clause}
                ORDER BY published_at DESC, scraped_at DESC
                LIMIT ? OFFSET ?""",
            [f"-{days}"] + params[1:],
        ).fetchall()
        return [dict(r) for r in rows]


def get_article_by_id(article_id: int) -> Optional[Dict[str, Any]]:
    """Return a single article dict or None."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM articles WHERE id = ?", (article_id,)
        ).fetchone()
        return dict(row) if row else None


def search_articles(
    query: str,
    days: int = 7,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """
    Keyword search over title (weight 3) and full_text (weight 1).
    Returns articles ordered by relevance score descending.
    """
    import re
    STOPWORDS = {
        "the","a","an","is","was","were","are","be","been","being",
        "what","who","when","where","why","how","tell","me","about",
        "more","some","any","all","this","that","these","those",
        "in","on","at","to","from","of","for","with","and","or","but",
        "did","do","does","can","could","would","should","will","have",
        "had","has","its","it","they","their","them","he","she","we",
        "i","my","your","please","find","show","give","latest","news",
        "today","yesterday","recent","happened","whats","what's",
    }
    tokens = [
        t for t in re.findall(r'\b[a-z]{3,}\b', query.lower())
        if t not in STOPWORDS
    ]
    if not tokens:
        # Fallback: return recent articles
        with get_conn() as conn:
            rows = conn.execute(
                """SELECT id, url, title, section, summary, full_text, published_at, scraped_at
                   FROM articles
                   WHERE scraped_at >= datetime('now', ? || ' days')
                   ORDER BY scraped_at DESC LIMIT ?""",
                [f"-{days}", limit],
            ).fetchall()
            return [dict(r) for r in rows]

    score_parts = []
    where_parts = []
    for token in tokens:
        score_parts += [
            f"CASE WHEN LOWER(title) LIKE '%{token}%' THEN 3 ELSE 0 END",
            f"CASE WHEN LOWER(full_text) LIKE '%{token}%' THEN 1 ELSE 0 END",
        ]
        where_parts.append(
            f"(LOWER(title) LIKE '%{token}%' OR LOWER(full_text) LIKE '%{token}%')"
        )

    score_expr = " + ".join(score_parts)
    where_expr = " OR ".join(where_parts)

    sql = f"""
        SELECT id, url, title, section, summary, full_text, published_at, scraped_at,
               ({score_expr}) AS relevance_score
        FROM articles
        WHERE scraped_at >= datetime('now', '-{days} days')
          AND ({where_expr})
        ORDER BY relevance_score DESC, published_at DESC
        LIMIT {limit}
    """
    with get_conn() as conn:
        rows = conn.execute(sql).fetchall()
        return [dict(r) for r in rows]


def delete_old_articles(days: int = 7) -> int:
    """Delete articles older than `days`; return count deleted."""
    with get_conn() as conn:
        cursor = conn.execute(
            "DELETE FROM articles WHERE scraped_at < datetime('now', ? || ' days')",
            [f"-{days}"],
        )
        count = cursor.rowcount
        if count:
            logger.info(f"Deleted {count} articles older than {days} days")
        return count


def get_all_urls() -> set:
    """Return set of all article URLs in DB."""
    with get_conn() as conn:
        rows = conn.execute("SELECT url FROM articles").fetchall()
        return {r["url"] for r in rows}


def save_chat_message(
    session_id: str,
    role: str,
    content: str,
    article_ids: Optional[List[int]] = None,
) -> int:
    """Insert a chat message row; return rowid."""
    with get_conn() as conn:
        cursor = conn.execute(
            """INSERT INTO chat_history (session_id, role, content, article_ids)
               VALUES (?, ?, ?, ?)""",
            (session_id, role, content, json.dumps(article_ids or [])),
        )
        return cursor.lastrowid


def get_chat_history(session_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Return the last `limit` messages for a session, ordered oldest-first."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT role, content, article_ids, created_at
               FROM chat_history
               WHERE session_id = ?
               ORDER BY created_at DESC
               LIMIT ?""",
            (session_id, limit),
        ).fetchall()
        return list(reversed([dict(r) for r in rows]))


def get_sections_summary() -> List[Dict[str, Any]]:
    """Return section counts and latest article date."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT section,
                      COUNT(*) AS count,
                      MAX(scraped_at) AS latest
               FROM articles
               WHERE scraped_at >= datetime('now', '-1 days')
               GROUP BY section
               ORDER BY count DESC"""
        ).fetchall()
        return [dict(r) for r in rows]


def get_article_count(days: int = 1) -> int:
    """Return count of articles fetched in the last `days` days."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM articles WHERE scraped_at >= datetime('now', ? || ' days')",
            [f"-{days}"],
        ).fetchone()
        return row["n"] if row else 0


def get_last_scraped() -> Optional[str]:
    """Return the most recent scraped_at timestamp."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT MAX(scraped_at) AS ts FROM articles"
        ).fetchone()
        return row["ts"] if row else None
