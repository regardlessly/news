"""
Flutter Mobile API — REST endpoints for the CNA Senior News Flutter app.

All articles are pre-filtered at ingestion for senior relevance (health,
CPF/retirement, HDB, cost of living, transport, safety, community events).

Mounted at /api/mobile/* in server.py.
All responses use a consistent envelope: {"data": ..., "meta": {...}}
OpenAPI schema available at /docs (Swagger UI) or /redoc.
"""
import logging
import threading
import time
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, List

import database

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mobile", tags=["Flutter Mobile API"])


# ---------------------------------------------------------------------------
# Response models (drives the OpenAPI schema)
# ---------------------------------------------------------------------------

class ArticleSummary(BaseModel):
    id: int = Field(..., example=42)
    url: str = Field(..., example="https://www.channelnewsasia.com/singapore/...")
    title: str = Field(..., example="Singapore announces new housing policy")
    section: str = Field(..., example="singapore")
    summary: Optional[str] = Field(None, example="The government unveiled...")
    published_at: Optional[str] = Field(None, example="2026-03-16T08:00:00")
    scraped_at: Optional[str] = Field(None, example="2026-03-16T09:15:00")


class ArticleDetail(ArticleSummary):
    full_text: Optional[str] = Field(None, example="Full article body text...")


class PaginationMeta(BaseModel):
    count: int = Field(..., example=30)
    limit: int = Field(..., example=30)
    offset: int = Field(..., example=0)


class ArticleListResponse(BaseModel):
    data: List[ArticleSummary]
    meta: PaginationMeta


class ArticleDetailMeta(BaseModel):
    pass

    class Config:
        extra = "allow"


class ArticleDetailResponse(BaseModel):
    data: ArticleDetail
    meta: dict = Field(default_factory=dict)


class SearchMeta(BaseModel):
    query: str = Field(..., example="housing policy")
    count: int = Field(..., example=5)


class SearchResponse(BaseModel):
    data: List[ArticleSummary]
    meta: SearchMeta


class SectionInfo(BaseModel):
    section: str = Field(..., example="singapore")
    count: int = Field(..., example=25)
    latest: Optional[str] = Field(None, example="2026-03-16T09:15:00")


class SectionsResponse(BaseModel):
    data: List[SectionInfo]
    meta: dict = Field(default_factory=dict)


class DigestArticle(BaseModel):
    id: int = Field(..., example=42)
    title: str = Field(..., example="Singapore announces new housing policy")
    summary: str = Field("", example="The government unveiled...")
    url: str = Field(..., example="https://www.channelnewsasia.com/singapore/...")
    published_at: Optional[str] = Field(None, example="2026-03-16T08:00:00")


class DigestGroup(BaseModel):
    section: str = Field(..., example="singapore")
    label: str = Field(..., example="Singapore")
    articles: List[DigestArticle]


class DigestMeta(BaseModel):
    total: int = Field(..., example=120)


class DigestResponse(BaseModel):
    data: List[DigestGroup]
    meta: DigestMeta


class StatusData(BaseModel):
    articles_today: int = Field(..., example=45)
    articles_week: int = Field(..., example=312)
    last_scraped: Optional[str] = Field(None, example="2026-03-16T09:15:00")


class StatusResponse(BaseModel):
    data: StatusData
    meta: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _envelope(data, **meta):
    return {"data": data, "meta": meta}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/articles",
    response_model=ArticleListResponse,
    summary="List senior-relevant articles",
    description="Paginated list of senior-relevant articles with optional section filter. All articles are pre-filtered at ingestion for topics seniors care about. Returns lightweight fields suitable for list/card views.",
)
def list_articles(
    section: Optional[str] = Query(None, description="Filter by section: singapore, asia, world, business, sport"),
    days: int = Query(1, ge=1, le=7, description="Look back N days"),
    limit: int = Query(30, ge=1, le=100, description="Max articles to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
):
    articles = database.get_articles(
        section=section if section and section != "all" else None,
        days=days,
        limit=limit,
        offset=offset,
    )
    return _envelope(articles, count=len(articles), limit=limit, offset=offset)


@router.get(
    "/articles/{article_id}",
    response_model=ArticleDetailResponse,
    summary="Get article detail",
    description="Full article including body text. Use for article detail / reader view.",
    responses={404: {"description": "Article not found"}},
)
def get_article(article_id: int):
    article = database.get_article_by_id(article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    return _envelope(article)


@router.get(
    "/search",
    response_model=SearchResponse,
    summary="Search senior-relevant articles",
    description="Keyword search across senior-relevant article titles (weighted 3x) and body text. Returns results ranked by relevance.",
)
def search_articles(
    q: str = Query(..., min_length=1, description="Search query"),
    days: int = Query(7, ge=1, le=30, description="Search window in days"),
    limit: int = Query(20, ge=1, le=50, description="Max results"),
):
    results = database.search_articles(query=q, days=days, limit=limit)
    return _envelope(results, query=q, count=len(results))


@router.get(
    "/sections",
    response_model=SectionsResponse,
    summary="List sections",
    description="Available news sections with today's article counts. Use for tab/filter UI.",
)
def get_sections():
    return _envelope(database.get_sections_summary())


# ---------------------------------------------------------------------------
# Mobile digest cache — pre-built in background, refreshed hourly
# ---------------------------------------------------------------------------

_SECTION_ORDER  = ["singapore", "asia", "world", "business", "sport"]
_SECTION_LABELS = {
    "singapore": "Singapore", "asia": "Asia", "world": "World",
    "business": "Business", "sport": "Sport",
}

_mobile_digest_cache: dict = {
    "groups":   [],
    "total":    0,
    "ready":    False,
    "building": False,
    "built_at": None,
}
_mobile_cache_lock = threading.Lock()


def _build_mobile_digest(days: int = 1) -> dict:
    """Build digest from DB — articles are already senior-filtered at ingestion time."""
    articles = database.get_articles(section=None, days=days, limit=200, offset=0)

    raw_groups: dict = {}
    for art in articles:
        s = (art.get("section") or "other").lower()
        raw_groups.setdefault(s, []).append(art)

    result = []
    ordered = [s for s in _SECTION_ORDER if s in raw_groups]
    extra = [s for s in raw_groups if s not in _SECTION_ORDER]

    for s in ordered + extra:
        label = _SECTION_LABELS.get(s, s.title())
        digest_articles = [{
            "id":           art["id"],
            "title":        art["title"],
            "summary":      art.get("summary") or "",
            "url":          art["url"],
            "published_at": art.get("published_at"),
        } for art in raw_groups[s]]

        if digest_articles:
            result.append({"section": s, "label": label, "articles": digest_articles})

    total = sum(len(g["articles"]) for g in result)
    return {"groups": result, "total": total}


def _refresh_mobile_digest():
    """Build the mobile digest cache, skip if already building."""
    with _mobile_cache_lock:
        if _mobile_digest_cache["building"]:
            return
        _mobile_digest_cache["building"] = True

    try:
        logger.info("Building mobile digest cache...")
        payload = _build_mobile_digest(days=1)
        with _mobile_cache_lock:
            _mobile_digest_cache["groups"]   = payload["groups"]
            _mobile_digest_cache["total"]    = payload["total"]
            _mobile_digest_cache["ready"]    = True
            _mobile_digest_cache["built_at"] = datetime.now(timezone.utc).isoformat()
        logger.info(f"Mobile digest cache ready — {len(payload['groups'])} sections, {payload['total']} articles.")
    except Exception as e:
        logger.error(f"Mobile digest cache build failed: {e}")
    finally:
        with _mobile_cache_lock:
            _mobile_digest_cache["building"] = False


def _mobile_digest_loop():
    """Refresh cache on startup, then every 60 minutes."""
    _refresh_mobile_digest()
    while True:
        time.sleep(3600)
        _refresh_mobile_digest()


# Start background thread
_mobile_bg = threading.Thread(target=_mobile_digest_loop, daemon=True)
_mobile_bg.start()


@router.get(
    "/digest",
    response_model=DigestResponse,
    summary="Get senior news digest",
    description="Senior-relevant articles grouped by section (Singapore, Asia, World, Business, Sport). Pre-built in background cache for instant response, refreshes hourly. Articles are pre-filtered at ingestion — no AI calls at request time.",
)
def get_digest():
    with _mobile_cache_lock:
        if not _mobile_digest_cache["ready"]:
            return _envelope([], total=0, ready=False, building=_mobile_digest_cache["building"])
        return _envelope(
            _mobile_digest_cache["groups"],
            total=_mobile_digest_cache["total"],
            built_at=_mobile_digest_cache["built_at"],
        )


@router.get(
    "/status",
    response_model=StatusResponse,
    summary="API status",
    description="Health check endpoint returning article counts and last scrape timestamp.",
)
def get_status():
    return _envelope({
        "articles_today": database.get_article_count(days=1),
        "articles_week":  database.get_article_count(days=7),
        "last_scraped":   database.get_last_scraped(),
    })
