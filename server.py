#!/usr/bin/env python3
"""
server.py — Merged CNA News server (viewer + chat in one process).

Routes:
  GET  /                          → news viewer UI
  GET  /chat                      → chat UI
  GET  /digest                    → senior digest UI
  GET  /viewer/static/*           → news viewer static files
  GET  /chat/static/*             → chat static files
  GET  /api/articles              → viewer API
  GET  /api/sections              → viewer API
  GET  /api/status                → shared status
  GET  /api/digest-summary        → senior digest API (cached)
  GET  /api/digest-status         → whether digest cache is ready
  POST /api/chat                  → chat API
  GET  /api/chat/history/{id}     → chat API

Usage (local):
  python server.py
  Open: http://localhost:8000       (news viewer)
        http://localhost:8000/chat  (chat)
        http://localhost:8000/digest (senior digest)

Usage (Railway):
  Set PORT env var — Railway assigns this automatically.
  Set DB_PATH=/data/news.db for persistent volume.
  Set DEEPSEEK_API_KEY for chat + summarisation.
"""
import os
import time
import logging
import threading
import uvicorn
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional

import database
import chat as chat_module
import summariser

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

database.init_db()

app = FastAPI(title="CNA News", docs_url=None, redoc_url=None)

# Static files — separate mount paths to avoid collision
app.mount("/viewer/static", StaticFiles(directory="news_viewer"), name="viewer_static")
app.mount("/chat/static",   StaticFiles(directory="chat_app"),   name="chat_static")


# ---- Digest cache ----
# Populated in background on startup; refreshed every hour.

SECTION_ORDER  = ["singapore", "asia", "world", "business", "sport"]
SECTION_ICONS  = {"singapore": "🇸🇬", "asia": "🌏", "world": "🌍", "business": "💼", "sport": "⚽"}
SECTION_LABELS = {"singapore": "Singapore", "asia": "Asia", "world": "World", "business": "Business", "sport": "Sport"}

_digest_cache: dict = {
    "groups":     [],
    "total":      0,
    "ready":      False,       # True once first build is complete
    "building":   False,       # True while a build is in progress
    "built_at":   None,        # UTC timestamp of last successful build
    "days":       1,
}
_cache_lock = threading.Lock()


def _build_digest(days: int = 1) -> dict:
    """
    Pull articles from DB, call DeepSeek per section, return digest payload.
    This is the slow part — runs in a background thread.
    """
    articles = database.get_articles(section=None, days=days, limit=200, offset=0)

    groups: dict = {}
    for art in articles:
        s = (art.get("section") or "other").lower()
        groups.setdefault(s, []).append(art)

    result = []
    ordered   = [s for s in SECTION_ORDER if s in groups]
    extra     = [s for s in groups if s not in SECTION_ORDER]

    for s in ordered + extra:
        arts  = groups[s]
        label = SECTION_LABELS.get(s, s.title())
        icon  = SECTION_ICONS.get(s, "📰")
        raw_summaries = [a.get("summary", "").strip() for a in arts if a.get("summary", "").strip()]

        logger.info(f"Building digest for section '{label}' ({len(raw_summaries)} summaries)...")
        digest = summariser.summarise_section(label, raw_summaries)
        if not digest:
            digest = " ".join(raw_summaries)

        links = [{"title": a["title"], "url": a["url"]} for a in arts]
        result.append({
            "section":       s,
            "label":         label,
            "icon":          icon,
            "summary":       digest,
            "article_count": len(arts),
            "articles":      links,
        })

    return {"groups": result, "total": len(articles)}


def _refresh_cache(days: int = 1, force: bool = False):
    """
    Build (or rebuild) the digest cache in a background thread.
    Skips if a build is already in progress unless force=True.
    """
    with _cache_lock:
        if _digest_cache["building"] and not force:
            logger.info("Digest cache build already in progress, skipping.")
            return
        _digest_cache["building"] = True

    try:
        logger.info(f"Starting digest cache build (days={days})...")
        payload = _build_digest(days=days)
        with _cache_lock:
            _digest_cache["groups"]   = payload["groups"]
            _digest_cache["total"]    = payload["total"]
            _digest_cache["ready"]    = True
            _digest_cache["built_at"] = datetime.now(timezone.utc).isoformat()
            _digest_cache["days"]     = days
        logger.info(f"Digest cache ready — {len(payload['groups'])} sections, {payload['total']} articles.")
    except Exception as e:
        logger.error(f"Digest cache build failed: {e}")
    finally:
        with _cache_lock:
            _digest_cache["building"] = False


def _background_refresh_loop():
    """Refresh the cache once at startup, then every 60 minutes."""
    _refresh_cache(days=1)
    while True:
        time.sleep(3600)          # 1 hour
        _refresh_cache(days=1)


# Start background thread immediately when the module loads
_bg_thread = threading.Thread(target=_background_refresh_loop, daemon=True)
_bg_thread.start()


# ---- UIs ----

@app.get("/")
def viewer_index():
    return FileResponse("news_viewer/index.html")


@app.get("/chat")
def chat_index():
    return FileResponse("chat_app/index.html")


@app.get("/digest")
def digest_index():
    return FileResponse("news_viewer/digest.html")


# ---- Viewer API ----

@app.get("/api/articles")
def get_articles(
    section: Optional[str] = Query(None),
    days: int = Query(1, ge=1, le=7),
    limit: int = Query(60, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    articles = database.get_articles(
        section=section if section and section != "all" else None,
        days=days,
        limit=limit,
        offset=offset,
    )
    return {"articles": articles, "count": len(articles)}


@app.get("/api/sections")
def get_sections():
    return {"sections": database.get_sections_summary()}


@app.get("/api/digest-status")
def get_digest_status():
    """Poll this to know whether the digest cache is ready."""
    with _cache_lock:
        return {
            "ready":    _digest_cache["ready"],
            "building": _digest_cache["building"],
            "built_at": _digest_cache["built_at"],
            "sections": len(_digest_cache["groups"]),
        }


@app.get("/api/digest-summary")
def get_digest_summary(days: int = Query(1, ge=1, le=7)):
    """
    Return one combined senior-focused summary per section.
    Serves from cache if ready; otherwise builds synchronously (first-time fallback).
    """
    with _cache_lock:
        cache_ready = _digest_cache["ready"]
        cache_days  = _digest_cache["days"]

    # Serve from cache if it covers the requested day range
    if cache_ready and cache_days == days:
        with _cache_lock:
            return {
                "groups":   _digest_cache["groups"],
                "total":    _digest_cache["total"],
                "built_at": _digest_cache["built_at"],
                "cached":   True,
            }

    # Cache miss (different days param or not yet built) — build synchronously
    # This path is rare; normal usage hits the cache.
    logger.info(f"Cache miss for days={days}, building synchronously...")
    payload = _build_digest(days=days)
    return {
        "groups":   payload["groups"],
        "total":    payload["total"],
        "built_at": datetime.now(timezone.utc).isoformat(),
        "cached":   False,
    }


@app.get("/api/digest")
def get_digest(days: int = Query(1, ge=1, le=7)):
    """Return today's articles grouped by section (raw, no AI summary)."""
    articles = database.get_articles(section=None, days=days, limit=200, offset=0)
    section_labels = {
        "singapore": "Singapore",
        "asia":      "Asia",
        "world":     "World",
        "business":  "Business",
        "sport":     "Sport",
    }
    groups = {}
    for art in articles:
        s = (art.get("section") or "other").lower()
        if s not in groups:
            groups[s] = []
        groups[s].append({
            "id":           art["id"],
            "title":        art["title"],
            "summary":      art.get("summary") or "",
            "url":          art["url"],
            "published_at": art.get("published_at"),
            "scraped_at":   art.get("scraped_at"),
        })
    result = []
    for s in SECTION_ORDER:
        if s in groups:
            result.append({"section": s, "label": section_labels.get(s, s.title()), "articles": groups[s]})
    for s, arts in groups.items():
        if s not in SECTION_ORDER:
            result.append({"section": s, "label": s.title(), "articles": arts})
    return {"groups": result, "total": len(articles)}


# ---- Shared status ----

@app.get("/api/status")
def get_status():
    return {
        "articles_today": database.get_article_count(days=1),
        "articles_week":  database.get_article_count(days=7),
        "last_scraped":   database.get_last_scraped(),
    }


# ---- Chat API ----

class ChatRequest(BaseModel):
    session_id: str
    message: str


@app.post("/api/chat")
def post_chat(req: ChatRequest):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    reply, article_ids = chat_module.chat(
        session_id=req.session_id,
        user_message=req.message.strip(),
    )

    sources = []
    for aid in article_ids:
        art = database.get_article_by_id(aid)
        if art:
            sources.append({
                "id":      art["id"],
                "title":   art["title"],
                "url":     art["url"],
                "section": art.get("section", ""),
            })

    return {
        "reply":      reply,
        "sources":    sources,
        "session_id": req.session_id,
    }


@app.get("/api/chat/history/{session_id}")
def get_history(session_id: str, limit: int = 40):
    return {"messages": database.get_chat_history(session_id=session_id, limit=limit)}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
