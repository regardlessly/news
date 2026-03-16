"""
Flutter Mobile API — clean REST endpoints for the CNA News Flutter app.

Mounted at /api/mobile/* in server.py.
All responses use a consistent envelope: {"data": ..., "meta": {...}}

OpenAPI schema available at /docs (Swagger UI) or /redoc.
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, List

import database
import summariser

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
    summary="List articles",
    description="Paginated article list with optional section filter. Returns lightweight fields suitable for list/card views.",
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
    summary="Search articles",
    description="Keyword search across article titles (weighted 3x) and body text. Returns results ranked by relevance.",
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


@router.get(
    "/digest",
    response_model=DigestResponse,
    summary="Get senior-focused news digest",
    description="Articles grouped by section, filtered to topics relevant to seniors (health, cost of living, CPF, HDB, transport, safety, etc). Uses AI to select the most relevant articles per section.",
)
def get_digest(
    days: int = Query(1, ge=1, le=7, description="Look back N days"),
    top_n: int = Query(10, ge=1, le=20, description="Max senior-relevant articles per section"),
):
    articles = database.get_articles(section=None, days=days, limit=200, offset=0)

    section_order = ["singapore", "asia", "world", "business", "sport"]
    section_labels = {
        "singapore": "Singapore", "asia": "Asia", "world": "World",
        "business": "Business", "sport": "Sport",
    }

    # Group raw articles by section
    raw_groups: dict = {}
    for art in articles:
        s = (art.get("section") or "other").lower()
        raw_groups.setdefault(s, [])
        raw_groups[s].append(art)

    # For each section, use AI to select only senior-relevant articles
    result = []
    ordered = [s for s in section_order if s in raw_groups]
    extra = [s for s in raw_groups if s not in section_order]

    for s in ordered + extra:
        label = section_labels.get(s, s.title())
        section_articles = raw_groups[s]

        # AI-powered senior relevance filtering
        selected = summariser.select_senior_articles(label, section_articles, top_n=top_n)

        # Condense summaries (~30% shorter) for mobile readability
        raw_summaries = [art.get("summary") or "" for art in selected]
        condensed = summariser.condense_summaries(raw_summaries, reduction=0.3)

        digest_articles = [{
            "id":           art["id"],
            "title":        art["title"],
            "summary":      condensed[i],
            "url":          art["url"],
            "published_at": art.get("published_at"),
        } for i, art in enumerate(selected)]

        if digest_articles:
            result.append({"section": s, "label": label, "articles": digest_articles})

    total_selected = sum(len(g["articles"]) for g in result)
    return _envelope(result, total=total_selected)


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
