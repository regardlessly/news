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
    "You are a friendly news editor writing a daily digest for seniors (aged 60+).\n\n"
    "Below are up to 10 article summaries from the '{section}' news section today.\n\n"
    "{summaries}\n\n"
    "Select ONLY the stories that are most relevant and interesting to seniors. "
    "Topics seniors care about include: health, healthcare, cost of living, government policies, "
    "CPF/retirement/pensions, housing (HDB), community events, family, social services, "
    "transport, safety, Singapore local news, and major world events that affect daily life. "
    "Skip stories about: celebrity gossip, gaming, youth trends, nightlife, extreme sports.\n\n"
    "Write a single cohesive digest (paragraph or bullet points) covering only the senior-relevant stories. Requirements:\n"
    "- Maximum 150 words\n"
    "- Friendly, warm, conversational tone — easy for anyone to understand\n"
    "- Use bullet points (starting with '- ') when there are 3 or more distinct topics, otherwise flowing prose\n"
    "- For bullet points, use '**Topic:**' style bold labels where helpful\n"
    "- Do not start with 'Today' or 'Here is'\n"
    "- Do not mention the number of articles or that you filtered anything\n"
    "- Write directly — no preamble like 'This section covers...'"
)


def summarise_section(
    section_label: str,
    article_summaries: List[str],
    max_input_chars: int = 6000,
) -> Optional[str]:
    """
    Produce a single ≤150-word senior-focused digest paragraph for a news section.
    article_summaries: list of individual article summary strings.
    Returns the digest string, or None on failure.
    """
    if not article_summaries:
        return None

    # Limit to top 10 to keep prompt focused and senior-relevant
    article_summaries = article_summaries[:10]

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
