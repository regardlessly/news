#!/usr/bin/env python3
"""
App 2 — CNA News Viewer Server

Serves the news viewer web app and provides read-only API endpoints.

Usage:
    python viewer_server.py
    Then open: http://localhost:8001
"""
import uvicorn
from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from typing import Optional

import database

database.init_db()

app = FastAPI(title="CNA News Viewer", docs_url=None, redoc_url=None)

# Serve static files for the viewer
app.mount("/static", StaticFiles(directory="news_viewer"), name="static")


@app.get("/")
def index():
    return FileResponse("news_viewer/index.html")


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
    sections = database.get_sections_summary()
    return {"sections": sections}


@app.get("/api/status")
def get_status():
    return {
        "articles_today": database.get_article_count(days=1),
        "articles_week": database.get_article_count(days=7),
        "last_scraped": database.get_last_scraped(),
    }


if __name__ == "__main__":
    uvicorn.run("viewer_server:app", host="0.0.0.0", port=8001, reload=False)
