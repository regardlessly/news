"""
Chat logic: keyword-based context retrieval + DeepSeek conversation.
"""
import re
import json
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple

from openai import OpenAI, APIError, RateLimitError

import config
import database
import summariser

logger = logging.getLogger(__name__)

_client: Optional[OpenAI] = None

CHAT_MODEL = "deepseek-chat"
MAX_CONTEXT_ARTICLES = 5
MAX_ARTICLE_TEXT_CHARS = 1500
MAX_HISTORY_TURNS = 6   # last N user+assistant pairs

STOPWORDS = {
    "the","a","an","is","was","were","are","be","been","being",
    "what","who","when","where","why","how","tell","me","about",
    "more","some","any","all","this","that","these","those",
    "in","on","at","to","from","of","for","with","and","or","but",
    "did","do","does","can","could","would","should","will","have",
    "had","has","its","it","they","their","them","he","she","we",
    "i","my","your","please","find","show","give","latest","news",
    "today","yesterday","recent","happened","whats","what's","give",
    "brief","summary","summarise","summarize","tell","know","about",
}

SYSTEM_PROMPT = """You are a warm, friendly news assistant talking with seniors. You have access to recent articles from Channel NewsAsia (CNA), Singapore's leading news outlet.

When answering:
- Base your answers on the provided article context
- If no relevant articles are provided, say so honestly
- Keep answers SHORT and conversational — like chatting with a friend, not writing a report
- Use simple, clear language — avoid jargon
- Cite the article title naturally in your answer (e.g. "According to CNA...")
- For follow-up questions, use both the article context and conversation history
- Aim for 2-3 short paragraphs at most

Today's date: {today}
"""


def get_client() -> OpenAI:
    global _client
    if _client is None:
        if not config.DEEPSEEK_API_KEY:
            raise ValueError("DEEPSEEK_API_KEY is not set in .env")
        _client = OpenAI(
            api_key=config.DEEPSEEK_API_KEY,
            base_url="https://api.deepseek.com",
        )
    return _client


def preprocess_query(query: str) -> List[str]:
    """Extract meaningful tokens from a query."""
    tokens = re.findall(r"\b[a-z]{3,}\b", query.lower())
    return [t for t in tokens if t not in STOPWORDS]


def find_relevant_articles(query: str, days: int = 7) -> List[Dict[str, Any]]:
    """Search DB for articles relevant to the query."""
    articles = database.search_articles(query=query, days=days, limit=MAX_CONTEXT_ARTICLES)
    return articles


def build_context_block(articles: List[Dict[str, Any]]) -> str:
    """Format articles as a readable context block for the LLM."""
    if not articles:
        return ""
    parts = []
    for i, art in enumerate(articles, 1):
        text = (art.get("full_text") or art.get("summary") or "")[:MAX_ARTICLE_TEXT_CHARS]
        pub = art.get("published_at") or art.get("scraped_at") or ""
        parts.append(
            f"[Article {i}]\n"
            f"Title: {art['title']}\n"
            f"Section: {art.get('section', '')} | Published: {pub}\n"
            f"{text}\n"
            f"---"
        )
    return "\n".join(parts)


def build_messages(
    session_history: List[Dict[str, Any]],
    user_message: str,
    context_articles: List[Dict[str, Any]],
    today: str,
) -> List[Dict[str, str]]:
    """Construct the messages list for DeepSeek."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(today=today)},
    ]

    if context_articles:
        context_block = build_context_block(context_articles)
        messages.append({
            "role": "system",
            "content": f"Here are the relevant CNA articles for this question:\n\n{context_block}",
        })

    # Last N turns of history
    history_turns = session_history[-(MAX_HISTORY_TURNS * 2):]
    for msg in history_turns:
        messages.append({
            "role": msg["role"],
            "content": msg["content"],
        })

    messages.append({"role": "user", "content": user_message})
    return messages


def chat(
    session_id: str,
    user_message: str,
    today: Optional[str] = None,
) -> Tuple[str, List[int]]:
    """
    Main chat function.
    Returns (assistant_reply, list_of_article_ids_used).
    Saves both turns to DB.
    """
    if not today:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Save user message first
    database.save_chat_message(session_id=session_id, role="user", content=user_message)

    # Find relevant articles
    articles = find_relevant_articles(user_message, days=7)
    article_ids = [a["id"] for a in articles]

    # Get session history (excluding the message we just saved)
    history = database.get_chat_history(session_id=session_id, limit=MAX_HISTORY_TURNS * 2 + 2)
    # Remove the last message (the user message we just inserted)
    if history and history[-1]["role"] == "user":
        history = history[:-1]

    messages = build_messages(
        session_history=history,
        user_message=user_message,
        context_articles=articles,
        today=today,
    )

    try:
        client = get_client()
        response = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=messages,
            temperature=0.5,
            max_tokens=1024,
        )
        raw_reply = response.choices[0].message.content.strip()
        # Condense into a short, conversational reply for seniors
        condensed = summariser.summarise_chat_reply(
            question=user_message,
            answer=raw_reply,
        )
        reply = condensed if condensed else raw_reply
    except RateLimitError as e:
        logger.error(f"DeepSeek rate limit: {e}")
        reply = "I'm temporarily rate-limited. Please try again in a moment."
        article_ids = []
    except APIError as e:
        logger.error(f"DeepSeek API error: {e}")
        reply = "Sorry, I encountered an error connecting to the AI service."
        article_ids = []
    except ValueError as e:
        logger.error(f"Config error: {e}")
        reply = "Chat is not configured. Please set DEEPSEEK_API_KEY in your .env file."
        article_ids = []

    # Save assistant reply
    database.save_chat_message(
        session_id=session_id,
        role="assistant",
        content=reply,
        article_ids=article_ids,
    )

    return reply, article_ids
