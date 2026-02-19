#!/usr/bin/env python3
"""
App 3 — CNA News Chat Server

Serves the chat web app and handles chat API requests.

Usage:
    python chat_server.py
    Then open: http://localhost:8002
"""
import os
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional

import database
import chat as chat_module

database.init_db()

app = FastAPI(title="CNA News Chat", docs_url=None, redoc_url=None)

# Serve static files for the chat app
app.mount("/static", StaticFiles(directory="chat_app"), name="static")


@app.get("/")
def index():
    return FileResponse("chat_app/index.html")


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

    # Fetch source article details for the frontend
    sources = []
    for aid in article_ids:
        art = database.get_article_by_id(aid)
        if art:
            sources.append({
                "id": art["id"],
                "title": art["title"],
                "url": art["url"],
                "section": art.get("section", ""),
            })

    return {
        "reply": reply,
        "sources": sources,
        "session_id": req.session_id,
    }


@app.get("/api/chat/history/{session_id}")
def get_history(session_id: str, limit: int = 40):
    messages = database.get_chat_history(session_id=session_id, limit=limit)
    return {"messages": messages}


@app.get("/api/status")
def get_status():
    return {
        "articles_today": database.get_article_count(days=1),
        "articles_week": database.get_article_count(days=7),
        "last_scraped": database.get_last_scraped(),
    }


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8002))
    uvicorn.run("chat_server:app", host="0.0.0.0", port=port, reload=False)
