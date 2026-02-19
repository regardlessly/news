"""
Shared database layer for all CNA News apps.

- If DATABASE_URL env var is set → uses PostgreSQL (Railway production)
- Otherwise → uses SQLite at DB_PATH (local development)

All function signatures are identical regardless of backend.
"""
import os
import json
import logging
import re
from contextlib import contextmanager
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")          # PostgreSQL on Railway
DB_PATH      = os.environ.get("DB_PATH", "news.db")   # SQLite fallback

USE_PG = bool(DATABASE_URL)
PGVECTOR_AVAILABLE = False   # set True at runtime if pgvector extension loads successfully

# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

@contextmanager
def get_conn():
    """Yield a database connection (PostgreSQL or SQLite)."""
    if USE_PG:
        import psycopg2
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = False
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    else:
        import sqlite3
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


def _ph() -> str:
    """Return the right placeholder: %s for PG, ? for SQLite."""
    return "%s" if USE_PG else "?"


def _fetchall(cursor) -> List[Dict[str, Any]]:
    """Convert cursor rows to list of dicts for both backends."""
    if USE_PG:
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]
    else:
        return [dict(r) for r in cursor.fetchall()]


def _fetchone(cursor) -> Optional[Dict[str, Any]]:
    """Convert a single cursor row to a dict."""
    if USE_PG:
        row = cursor.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cursor.description]
        return dict(zip(cols, row))
    else:
        row = cursor.fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Create tables and indexes if they don't exist."""
    # --- Block 1: Core tables (always runs, never touches pgvector) ---
    with get_conn() as conn:
        cur = conn.cursor()
        if USE_PG:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS articles (
                    id           SERIAL PRIMARY KEY,
                    url          TEXT    UNIQUE NOT NULL,
                    title        TEXT    NOT NULL,
                    section      TEXT    NOT NULL,
                    summary      TEXT,
                    full_text    TEXT,
                    published_at TEXT,
                    scraped_at   TIMESTAMP NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_articles_section ON articles(section)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_articles_scraped_at ON articles(scraped_at)")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS chat_history (
                    id           SERIAL PRIMARY KEY,
                    session_id   TEXT      NOT NULL,
                    role         TEXT      NOT NULL CHECK(role IN ('user','assistant')),
                    content      TEXT      NOT NULL,
                    article_ids  TEXT,
                    created_at   TIMESTAMP NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_session ON chat_history(session_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_created ON chat_history(created_at)")
        else:
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
                CREATE INDEX IF NOT EXISTS idx_articles_section ON articles(section);
                CREATE INDEX IF NOT EXISTS idx_articles_scraped_at ON articles(scraped_at);

                CREATE TABLE IF NOT EXISTS chat_history (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id   TEXT    NOT NULL,
                    role         TEXT    NOT NULL CHECK(role IN ('user','assistant')),
                    content      TEXT    NOT NULL,
                    article_ids  TEXT,
                    created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
                );
                CREATE INDEX IF NOT EXISTS idx_chat_session ON chat_history(session_id);
                CREATE INDEX IF NOT EXISTS idx_chat_created ON chat_history(created_at);
            """)

    # --- Block 2: pgvector (optional — separate connection so failure doesn't affect Block 1) ---
    if USE_PG:
        global PGVECTOR_AVAILABLE
        try:
            with get_conn() as conn:
                cur = conn.cursor()
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                cur.execute("ALTER TABLE articles ADD COLUMN IF NOT EXISTS embedding vector(1536)")
                cur.execute("""
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM pg_indexes
                            WHERE tablename = 'articles' AND indexname = 'idx_articles_embedding'
                        ) AND (SELECT COUNT(*) FROM articles) >= 100 THEN
                            EXECUTE 'CREATE INDEX idx_articles_embedding
                                     ON articles USING ivfflat (embedding vector_cosine_ops)
                                     WITH (lists = 100)';
                        END IF;
                    END$$;
                """)
            PGVECTOR_AVAILABLE = True
            logger.info("pgvector enabled — semantic search active")
        except Exception as e:
            logger.warning(f"pgvector not available, using keyword search: {e}")


# ---------------------------------------------------------------------------
# Articles
# ---------------------------------------------------------------------------

def article_exists(url: str) -> bool:
    """Return True if an article with this URL is already in the DB."""
    ph = _ph()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT 1 FROM articles WHERE url = {ph}", (url,))
        return cur.fetchone() is not None


def insert_article(
    url: str,
    title: str,
    section: str,
    full_text: str,
    published_at: Optional[str] = None,
    summary: Optional[str] = None,
) -> int:
    """Insert a new article; return its id. Raises on duplicate url."""
    with get_conn() as conn:
        cur = conn.cursor()
        if USE_PG:
            cur.execute(
                """INSERT INTO articles (url, title, section, full_text, published_at, summary)
                   VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                (url, title, section, full_text, published_at, summary),
            )
            return cur.fetchone()[0]
        else:
            cur.execute(
                """INSERT INTO articles (url, title, section, full_text, published_at, summary)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (url, title, section, full_text, published_at, summary),
            )
            return cur.lastrowid


def update_summary(article_id: int, summary: str) -> None:
    """Set the summary field for an article."""
    ph = _ph()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"UPDATE articles SET summary = {ph} WHERE id = {ph}",
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
        cur = conn.cursor()
        if USE_PG:
            section_clause = ""
            params: list = [str(days)]
            if section and section != "all":
                section_clause = "AND section = %s"
                params.append(section)
            params += [limit, offset]
            cur.execute(
                f"""SELECT id, url, title, section, summary, published_at, scraped_at
                    FROM articles
                    WHERE scraped_at >= NOW() - (%s || ' days')::INTERVAL
                    {section_clause}
                    ORDER BY published_at DESC, scraped_at DESC
                    LIMIT %s OFFSET %s""",
                params,
            )
        else:
            params = [f"-{days}"]
            section_clause = ""
            if section and section != "all":
                section_clause = "AND section = ?"
                params.append(section)
            params += [limit, offset]
            cur.execute(
                f"""SELECT id, url, title, section, summary, published_at, scraped_at
                    FROM articles
                    WHERE scraped_at >= datetime('now', ? || ' days')
                    {section_clause}
                    ORDER BY published_at DESC, scraped_at DESC
                    LIMIT ? OFFSET ?""",
                params,
            )
        return _fetchall(cur)


def get_article_by_id(article_id: int) -> Optional[Dict[str, Any]]:
    """Return a single article dict or None."""
    ph = _ph()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM articles WHERE id = {ph}", (article_id,))
        return _fetchone(cur)


def search_articles(
    query: str,
    days: int = 7,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Keyword search over title (weight 3) and full_text (weight 1)."""
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

    with get_conn() as conn:
        cur = conn.cursor()
        if not tokens:
            # Fallback: return recent articles
            if USE_PG:
                cur.execute(
                    """SELECT id, url, title, section, summary, full_text, published_at, scraped_at
                       FROM articles
                       WHERE scraped_at >= NOW() - (%s || ' days')::INTERVAL
                       ORDER BY scraped_at DESC LIMIT %s""",
                    [str(days), limit],
                )
            else:
                cur.execute(
                    """SELECT id, url, title, section, summary, full_text, published_at, scraped_at
                       FROM articles
                       WHERE scraped_at >= datetime('now', ? || ' days')
                       ORDER BY scraped_at DESC LIMIT ?""",
                    [f"-{days}", limit],
                )
            return _fetchall(cur)

        # Build scoring query — tokens are validated [a-z]{3,} so safe to interpolate
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

        if USE_PG:
            cur.execute(
                f"""SELECT id, url, title, section, summary, full_text, published_at, scraped_at,
                           ({score_expr}) AS relevance_score
                    FROM articles
                    WHERE scraped_at >= NOW() - ('{days} days')::INTERVAL
                      AND ({where_expr})
                    ORDER BY relevance_score DESC, published_at DESC
                    LIMIT {limit}"""
            )
        else:
            cur.execute(
                f"""SELECT id, url, title, section, summary, full_text, published_at, scraped_at,
                           ({score_expr}) AS relevance_score
                    FROM articles
                    WHERE scraped_at >= datetime('now', '-{days} days')
                      AND ({where_expr})
                    ORDER BY relevance_score DESC, published_at DESC
                    LIMIT {limit}"""
            )
        return _fetchall(cur)


def delete_old_articles(days: int = 7) -> int:
    """Delete articles older than `days`; return count deleted."""
    with get_conn() as conn:
        cur = conn.cursor()
        if USE_PG:
            cur.execute(
                "DELETE FROM articles WHERE scraped_at < NOW() - (%s || ' days')::INTERVAL",
                (str(days),),
            )
        else:
            cur.execute(
                "DELETE FROM articles WHERE scraped_at < datetime('now', ? || ' days')",
                (f"-{days}",),
            )
        count = cur.rowcount
        if count:
            logger.info(f"Deleted {count} articles older than {days} days")
        return count


def get_all_urls() -> set:
    """Return set of all article URLs in DB."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT url FROM articles")
        rows = cur.fetchall()
        return {row[0] for row in rows} if USE_PG else {row["url"] for row in rows}


def get_article_index(days: int = 7, limit: int = 200) -> List[Dict[str, Any]]:
    """Return lightweight article list (id, title, section, summary) for the past N days."""
    with get_conn() as conn:
        cur = conn.cursor()
        if USE_PG:
            cur.execute(
                """SELECT id, title, section, summary, published_at
                   FROM articles
                   WHERE scraped_at >= NOW() - (%s || ' days')::INTERVAL
                   ORDER BY published_at DESC, scraped_at DESC
                   LIMIT %s""",
                [str(days), limit],
            )
        else:
            cur.execute(
                """SELECT id, title, section, summary, published_at
                   FROM articles
                   WHERE scraped_at >= datetime('now', ? || ' days')
                   ORDER BY published_at DESC, scraped_at DESC
                   LIMIT ?""",
                [f"-{days}", limit],
            )
        return _fetchall(cur)


def update_embedding(article_id: int, embedding: List[float]) -> None:
    """Store a precomputed embedding vector for an article (PostgreSQL + pgvector only)."""
    if not USE_PG or not PGVECTOR_AVAILABLE:
        return
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE articles SET embedding = %s WHERE id = %s",
            (embedding, article_id),
        )


def search_articles_semantic(
    query_embedding: List[float],
    days: int = 7,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """Return articles ranked by cosine similarity to the query embedding (pgvector only)."""
    if not USE_PG or not PGVECTOR_AVAILABLE:
        return []
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""SELECT id, url, title, section, summary, full_text, published_at, scraped_at
                FROM articles
                WHERE scraped_at >= NOW() - ('{days} days')::INTERVAL
                  AND embedding IS NOT NULL
                ORDER BY embedding <=> %s
                LIMIT %s""",
            (query_embedding, limit),
        )
        return _fetchall(cur)


def get_articles_without_embedding(limit: int = 500) -> List[Dict[str, Any]]:
    """Return articles that have a summary but no embedding yet (pgvector only)."""
    if not USE_PG or not PGVECTOR_AVAILABLE:
        return []
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT id, title, summary FROM articles
               WHERE summary IS NOT NULL AND summary != ''
                 AND embedding IS NULL
               ORDER BY scraped_at DESC
               LIMIT %s""",
            (limit,),
        )
        return _fetchall(cur)


def get_unsummarised_articles(limit: int = 500) -> List[Dict[str, Any]]:
    """Return articles that have no summary yet, newest first."""
    ph = _ph()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""SELECT id, title, full_text FROM articles
               WHERE summary IS NULL OR summary = ''
               ORDER BY scraped_at DESC
               LIMIT {ph}""",
            (limit,),
        )
        return _fetchall(cur)


def get_article_count(days: int = 1) -> int:
    """Return count of articles fetched in the last `days` days."""
    with get_conn() as conn:
        cur = conn.cursor()
        if USE_PG:
            cur.execute(
                "SELECT COUNT(*) FROM articles WHERE scraped_at >= NOW() - (%s || ' days')::INTERVAL",
                (str(days),),
            )
        else:
            cur.execute(
                "SELECT COUNT(*) AS n FROM articles WHERE scraped_at >= datetime('now', ? || ' days')",
                (f"-{days}",),
            )
        row = cur.fetchone()
        return (row[0] if USE_PG else row["n"]) if row else 0


def get_last_scraped() -> Optional[str]:
    """Return the most recent scraped_at timestamp as a string."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT MAX(scraped_at) FROM articles")
        row = cur.fetchone()
        val = row[0] if row else None
        return str(val) if val else None


def get_sections_summary() -> List[Dict[str, Any]]:
    """Return section counts and latest article date (today only)."""
    with get_conn() as conn:
        cur = conn.cursor()
        if USE_PG:
            cur.execute(
                """SELECT section,
                          COUNT(*) AS count,
                          MAX(scraped_at) AS latest
                   FROM articles
                   WHERE scraped_at >= NOW() - '1 day'::INTERVAL
                   GROUP BY section
                   ORDER BY count DESC"""
            )
        else:
            cur.execute(
                """SELECT section,
                          COUNT(*) AS count,
                          MAX(scraped_at) AS latest
                   FROM articles
                   WHERE scraped_at >= datetime('now', '-1 days')
                   GROUP BY section
                   ORDER BY count DESC"""
            )
        return _fetchall(cur)


# ---------------------------------------------------------------------------
# Chat history
# ---------------------------------------------------------------------------

def save_chat_message(
    session_id: str,
    role: str,
    content: str,
    article_ids: Optional[List[int]] = None,
) -> int:
    """Insert a chat message row; return its id."""
    with get_conn() as conn:
        cur = conn.cursor()
        if USE_PG:
            cur.execute(
                """INSERT INTO chat_history (session_id, role, content, article_ids)
                   VALUES (%s, %s, %s, %s) RETURNING id""",
                (session_id, role, content, json.dumps(article_ids or [])),
            )
            return cur.fetchone()[0]
        else:
            cur.execute(
                """INSERT INTO chat_history (session_id, role, content, article_ids)
                   VALUES (?, ?, ?, ?)""",
                (session_id, role, content, json.dumps(article_ids or [])),
            )
            return cur.lastrowid


def get_chat_history(session_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Return the last `limit` messages for a session, ordered oldest-first."""
    ph = _ph()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""SELECT role, content, article_ids, created_at
               FROM chat_history
               WHERE session_id = {ph}
               ORDER BY created_at DESC
               LIMIT {ph}""",
            (session_id, limit),
        )
        return list(reversed(_fetchall(cur)))
