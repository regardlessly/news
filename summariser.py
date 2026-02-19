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


SECTION_DIGEST_PROMPT = (
    "You are a friendly news editor writing a daily digest for general readers.\n\n"
    "Below are individual article summaries from the '{section}' news section today.\n\n"
    "{summaries}\n\n"
    "Write a single cohesive digest paragraph (or use bullet points where helpful) that captures "
    "the most important stories. Requirements:\n"
    "- Maximum 150 words\n"
    "- Friendly, clear, conversational tone — easy for anyone to understand\n"
    "- Use bullet points for 3 or more distinct topics, otherwise flowing prose is fine\n"
    "- Do not start with 'Today' or 'Here is'\n"
    "- Do not mention the number of articles\n"
    "- Write directly — no preamble like 'This section covers...'"
)


def summarise_section(
    section_label: str,
    article_summaries: List[str],
    max_input_chars: int = 6000,
) -> Optional[str]:
    """
    Produce a single ≤150-word digest paragraph for a news section.
    article_summaries: list of individual article summary strings.
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
            max_tokens=250,
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
                max_tokens=250,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Section digest retry failed: {e}")
            return None
    except Exception as e:
        logger.error(f"Section digest error: {e}")
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
