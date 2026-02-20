"""
DeepSeek-powered article summarisation.
"""
import time
import logging
from typing import Optional, Dict, Callable, List
from openai import OpenAI, RateLimitError, APIError

import config

logger = logging.getLogger(__name__)

_client: Optional[OpenAI] = None
_embed_client: Optional[OpenAI] = None

SUMMARISE_PROMPT = (
    "Summarise the following news article in 2-3 concise sentences. "
    "Focus on: what happened, who is involved, and why it matters. "
    "Do not start with 'This article' or 'The article'. Write directly. "
    "Keep it under 80 words.\n\nTitle: {title}\n\nArticle:\n{text}"
)


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


def get_embed_client() -> OpenAI:
    global _embed_client
    if _embed_client is None:
        if not config.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY is not set in .env")
        _embed_client = OpenAI(api_key=config.OPENAI_API_KEY)
    return _embed_client


def embed_texts(texts: List[str]) -> List[List[float]]:
    """
    Embed a list of texts using OpenAI text-embedding-3-small (1536 dims).
    Returns one float vector per input text.
    Raises on API error.
    """
    client = get_embed_client()
    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=texts,
    )
    # Sort by index to preserve input order
    items = sorted(response.data, key=lambda x: x.index)
    return [item.embedding for item in items]


def summarise_article(
    title: str,
    full_text: str,
    max_text_chars: int = 4000,
) -> Optional[str]:
    """
    Summarise a single article. Returns summary string or None on failure.
    """
    text = full_text[:max_text_chars]
    prompt = SUMMARISE_PROMPT.format(title=title, text=text)

    try:
        client = get_client()
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=200,
        )
        summary = response.choices[0].message.content.strip()
        return summary
    except RateLimitError:
        logger.warning("Rate limit hit, waiting 60s...")
        time.sleep(60)
        # Retry once
        try:
            client = get_client()
            response = client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=200,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Retry failed: {e}")
            return None
    except APIError as e:
        logger.error(f"DeepSeek API error: {e}")
        return None
    except Exception as e:
        logger.error(f"Summarisation error: {e}")
        return None


SENIOR_SELECT_PROMPT = (
    "You are a news editor curating content for seniors (aged 60+) in Singapore.\n\n"
    "Below is a numbered list of article titles from the '{section}' news section today.\n\n"
    "{titles}\n\n"
    "Pick the TOP 10 articles most relevant and useful to seniors. "
    "Seniors care about: health, healthcare, cost of living, government policies, "
    "CPF/retirement/pensions, housing (HDB), community events, family, social services, "
    "transport, safety, Singapore local news, and major world events that affect daily life.\n"
    "Skip: celebrity gossip, gaming, youth trends, nightlife, extreme sports.\n\n"
    "Reply with ONLY a JSON array of the selected article numbers (1-based), e.g. [1,3,5,7,9,11,13,15,17,20]. "
    "No explanation, no text, just the JSON array."
)

SECTION_DIGEST_PROMPT = (
    "You are a news editor writing a concise daily digest.\n\n"
    "Below are article summaries from the '{section}' news section today.\n\n"
    "{summaries}\n\n"
    "Write a bullet-point digest covering the key stories. Requirements:\n"
    "- Always use bullet points (starting with '- '), one per story\n"
    "- Maximum 5 bullets\n"
    "- Each bullet: maximum 20 words\n"
    "- Use '**Topic:**' bold label at the start of each bullet where helpful\n"
    "- Plain, clear language — no jargon\n"
    "- Do not start with 'Today', 'Here is', or any preamble\n"
    "- Do not mention the number of articles"
)


def select_senior_articles(
    section_label: str,
    articles: List[Dict],
    top_n: int = 10,
) -> List[Dict]:
    """
    Use DeepSeek to pick the top_n most senior-relevant articles from a list.
    articles: list of dicts with at least 'title' and 'summary' keys.
    Returns a filtered list of up to top_n articles.
    Falls back to the first top_n articles if the API call fails.
    """
    if len(articles) <= top_n:
        return articles

    titles = "\n".join(f"{i+1}. {a['title']}" for i, a in enumerate(articles))
    prompt = SENIOR_SELECT_PROMPT.format(section=section_label, titles=titles)

    import json as _json
    import re as _re

    def _call():
        client = get_client()
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=100,
        )
        return resp.choices[0].message.content.strip()

    try:
        raw = _call()
    except RateLimitError:
        logger.warning("Rate limit on senior article selection, retrying in 60s...")
        time.sleep(60)
        try:
            raw = _call()
        except Exception as e:
            logger.error(f"Senior article selection retry failed: {e}")
            return articles[:top_n]
    except Exception as e:
        logger.error(f"Senior article selection error: {e}")
        return articles[:top_n]

    # Parse the JSON array from the response
    try:
        # Extract first JSON array found in the response
        match = _re.search(r'\[[\d,\s]+\]', raw)
        if not match:
            raise ValueError(f"No JSON array in response: {raw!r}")
        indices = _json.loads(match.group())
        # Convert 1-based to 0-based, clamp to valid range
        selected = []
        for idx in indices:
            i = int(idx) - 1
            if 0 <= i < len(articles):
                selected.append(articles[i])
        if not selected:
            raise ValueError("Empty selection after parsing")
        return selected[:top_n]
    except Exception as e:
        logger.error(f"Failed to parse senior article selection ({e}), using first {top_n}")
        return articles[:top_n]


def summarise_section(
    section_label: str,
    article_summaries: List[str],
    max_input_chars: int = 6000,
) -> Optional[str]:
    """
    Produce a single ≤150-word digest paragraph for a news section.
    article_summaries: list of pre-selected article summary strings (≤10).
    Returns the digest string, or None on failure.
    """
    if not article_summaries:
        return None

    # Join summaries, truncate to avoid huge prompts
    joined = "\n".join(f"- {s}" for s in article_summaries)
    joined = joined[:max_input_chars]

    prompt = SECTION_DIGEST_PROMPT.format(
        section=section_label,
        summaries=joined,
    )

    try:
        client = get_client()
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
            max_tokens=180,
        )
        return response.choices[0].message.content.strip()
    except RateLimitError:
        logger.warning("Rate limit hit on section digest, waiting 60s...")
        time.sleep(60)
        try:
            client = get_client()
            response = client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.4,
                max_tokens=180,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Section digest retry failed: {e}")
            return None
    except Exception as e:
        logger.error(f"Section digest error: {e}")
        return None


CHAT_REPLY_PROMPT = (
    "You are a warm, friendly news assistant talking to a senior reader.\n\n"
    "The reader asked: {question}\n\n"
    "Here is the detailed answer:\n{answer}\n\n"
    "Rewrite this as a SHORT, conversational response — like explaining to a friend over coffee. "
    "Requirements:\n"
    "- Maximum 120 words\n"
    "- Friendly, simple language — no jargon\n"
    "- Keep the key facts but cut everything else\n"
    "- Use plain sentences, not bullet points\n"
    "- Do not start with 'Sure' or 'Certainly' or 'Of course'\n"
    "- Write directly as the final reply (do not add preamble)"
)


def summarise_chat_reply(
    question: str,
    answer: str,
) -> Optional[str]:
    """
    Condense a verbose chat answer into a short, conversational reply for seniors.
    Returns the condensed reply, or None on failure (caller should use original answer).
    """
    # Only condense if the answer is long enough to warrant it
    if len(answer.split()) < 60:
        return None

    prompt = CHAT_REPLY_PROMPT.format(question=question, answer=answer)

    try:
        client = get_client()
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
            max_tokens=200,
        )
        return response.choices[0].message.content.strip()
    except RateLimitError:
        logger.warning("Rate limit hit on chat reply summarisation, waiting 60s...")
        time.sleep(60)
        try:
            client = get_client()
            response = client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.4,
                max_tokens=200,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Chat reply summarisation retry failed: {e}")
            return None
    except Exception as e:
        logger.error(f"Chat reply summarisation error: {e}")
        return None


def summarise_batch(
    articles: List[Dict],
    update_fn: Callable[[int, str], None],
    delay_seconds: float = 0.5,
) -> Dict[str, int]:
    """
    Summarise a list of articles sequentially.
    articles: list of {"id": int, "title": str, "full_text": str}
    update_fn: called with (article_id, summary) after each success.
    Returns {"success": n, "failed": m}.
    """
    success = 0
    failed = 0

    for art in articles:
        summary = summarise_article(
            title=art["title"],
            full_text=art.get("full_text", ""),
        )
        if summary:
            update_fn(art["id"], summary)
            success += 1
            logger.info(f"Summarised article {art['id']}: {art['title'][:50]}")
        else:
            failed += 1
            logger.warning(f"Failed to summarise article {art['id']}")

        time.sleep(delay_seconds)

    return {"success": success, "failed": failed}
