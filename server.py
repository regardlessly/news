#!/usr/bin/env python3
"""
server.py — Merged CNA News server (viewer + chat in one process).

Routes:
  GET  /                          → news viewer UI
  GET  /chat                      → chat UI
  GET  /viewer/static/*           → news viewer static files
  GET  /chat/static/*             → chat static files
  GET  /api/articles              → viewer API
  GET  /api/sections              → viewer API
  GET  /api/status                → shared status
  POST /api/chat                  → chat API
  GET  /api/chat/history/{id}     → chat API

Usage (local):
  python server.py
  Open: http://localhost:8000       (news viewer)
        http://localhost:8000/chat  (chat)

Usage (Railway):
  Set PORT env var — Railway assigns this automatically.
  Set DB_PATH=/data/news.db for persistent volume.
  Set DEEPSEEK_API_KEY for chat + summarisation.
"""
import os
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional

import database
import chat as chat_module

database.init_db()

app = FastAPI(title="CNA News", docs_url=None, redoc_url=None)

# Static files — separate mount paths to avoid collision
app.mount("/viewer/static", StaticFiles(directory="news_viewer"), name="viewer_static")
app.mount("/chat/static",   StaticFiles(directory="chat_app"),   name="chat_static")


# ---- UIs ----

@app.get("/")
def viewer_index():
    return FileResponse("news_viewer/index.html")


@app.get("/chat")
def chat_index():
    return FileResponse("chat_app/index.html")


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
