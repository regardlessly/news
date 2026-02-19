"""
CNA (Channel NewsAsia) scraper.
Scrapes article links from section pages and parses article content.
"""
import re
import json
import time
import logging
import requests
from bs4 import BeautifulSoup
from typing import Optional, Dict, Any, List, Set
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

BASE_URL = "https://www.channelnewsasia.com"

# Section slug -> display name. Empty string = homepage.
SECTIONS: Dict[str, str] = {
    "":          "Top Stories",
    "singapore": "Singapore",
    "asia":      "Asia",
    "world":     "World",
    "business":  "Business",
    "sport":     "Sport",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.channelnewsasia.com/",
}

REQUEST_TIMEOUT = 20
INTER_REQUEST_DELAY = 1.2  # seconds between page fetches

# Paths to skip — videos, podcasts, topic pages, etc.
SKIP_PREFIXES = (
    "/watch", "/shorts", "/podcasts", "/live",
    "/topic", "/author", "/profile", "/search",
    "/about", "/advertise", "/tag",
    "/news/businessblueprint", "/news/healthmatters",
    "/news/theasiantraveller",
)

# Article URLs always end with a numeric ID suffix
ARTICLE_ID_PATTERN = re.compile(r'-\d{6,}$')

# URL path prefix → canonical section name
URL_SECTION_MAP: Dict[str, str] = {
    "singapore":    "singapore",
    "asia":         "asia",
    "east-asia":    "asia",
    "south-asia":   "asia",
    "world":        "world",
    "business":     "business",
    "sport":        "sport",
    "commentary":   "singapore",
    "entertainment":"singapore",
    "dining":       "singapore",
    "women":        "singapore",
    "style-beauty": "singapore",
    "news":         "singapore",
    "cna-insider":  "world",
    "cna-lifestyle": "singapore",
}


def make_session() -> requests.Session:
    """Return a requests Session with retry logic."""
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(HEADERS)
    return session


_session: Optional[requests.Session] = None


def get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = make_session()
    return _session


def is_valid_article_href(href: str) -> bool:
    """Return True if href looks like a CNA article path."""
    if not href or not href.startswith("/"):
        return False
    if any(href.startswith(p) for p in SKIP_PREFIXES):
        return False
    if not ARTICLE_ID_PATTERN.search(href):
        return False
    return True


def scrape_section_links(section_slug: str) -> List[str]:
    """
    Fetch a CNA section page and return unique article URLs.
    section_slug = "" for homepage, "singapore", "asia", etc.
    """
    url = f"{BASE_URL}/{section_slug}" if section_slug else BASE_URL
    try:
        resp = get_session().get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning(f"Failed to fetch section '{section_slug}': {e}")
        return []

    soup = BeautifulSoup(resp.text, "lxml")

    seen: Set[str] = set()
    links: List[str] = []

    # Primary selector: heading links in list objects
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        if not href.startswith("/"):
            continue
        # Normalise: strip query/fragment
        href = href.split("?")[0].split("#")[0]
        if not is_valid_article_href(href):
            continue
        full_url = BASE_URL + href
        if full_url not in seen:
            seen.add(full_url)
            links.append(full_url)

    logger.info(f"Section '{section_slug or 'home'}': found {len(links)} article links")
    return links


def infer_section_from_url(url: str) -> str:
    """Extract canonical section label from article URL."""
    path = url.replace(BASE_URL, "").lstrip("/")
    first_segment = path.split("/")[0] if "/" in path else path
    return URL_SECTION_MAP.get(first_segment, "singapore")


def parse_article_page(url: str) -> Optional[Dict[str, Any]]:
    """
    Fetch and parse a single CNA article page.
    Returns dict with: url, title, full_text, published_at, section
    Returns None on failure or if not enough content found.
    """
    try:
        resp = get_session().get(url, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning(f"Failed to fetch article {url}: {e}")
        return None

    soup = BeautifulSoup(resp.text, "lxml")

    # 1. Title
    title = None
    h1 = soup.find("h1", class_=lambda c: c and "h1--page-title" in c)
    if h1:
        title = h1.get_text(strip=True)
    if not title:
        og = soup.find("meta", property="og:title")
        title = og.get("content", "").strip() if og else None
    if not title:
        return None

    # 2. Full text
    full_text = ""
    content_section = soup.find("section", attrs={"data-title": "Content"})
    if content_section:
        text_long = content_section.find("div", class_="text-long")
        if text_long:
            paragraphs = [
                p.get_text(strip=True)
                for p in text_long.find_all("p")
                if len(p.get_text(strip=True)) > 30
            ]
            full_text = "\n\n".join(paragraphs)

    # Fallback: try generic article body selectors
    if not full_text:
        for selector in [
            {"class_": lambda c: c and "article-body" in c},
            {"class_": lambda c: c and "content-wrapper" in c},
        ]:
            el = soup.find("div", **selector)
            if el:
                paragraphs = [
                    p.get_text(strip=True)
                    for p in el.find_all("p")
                    if len(p.get_text(strip=True)) > 30
                ]
                if paragraphs:
                    full_text = "\n\n".join(paragraphs)
                    break

    # Fallback: og:description as minimal content
    if not full_text:
        og_desc = soup.find("meta", property="og:description")
        if og_desc:
            full_text = og_desc.get("content", "").strip()

    if not full_text:
        logger.debug(f"No content found for {url}")
        return None

    # 3. Published date (JSON-LD is most reliable)
    published_at = None
    ld_script = soup.find("script", type="application/ld+json")
    if ld_script and ld_script.string:
        try:
            ld = json.loads(ld_script.string)
            graph = ld.get("@graph", [])
            for node in graph:
                if node.get("datePublished"):
                    published_at = node["datePublished"]
                    break
            if not published_at and ld.get("datePublished"):
                published_at = ld["datePublished"]
        except (json.JSONDecodeError, AttributeError):
            pass

    if not published_at:
        og_time = soup.find("meta", property="article:published_time")
        if og_time:
            published_at = og_time.get("content", "")

    # 4. Section from URL
    section = infer_section_from_url(url)

    return {
        "url": url,
        "title": title,
        "full_text": full_text,
        "published_at": published_at,
        "section": section,
    }


def scrape_all(
    existing_urls: Set[str],
    progress_callback=None,
) -> List[Dict[str, Any]]:
    """
    Scrape all configured CNA sections.
    Skips URLs already in existing_urls.
    Returns list of article dicts ready for DB insertion.
    """
    all_links: Set[str] = set()

    # Collect links from all sections
    for slug in SECTIONS:
        links = scrape_section_links(slug)
        all_links.update(links)
        time.sleep(INTER_REQUEST_DELAY)

    new_links = [u for u in all_links if u not in existing_urls]
    logger.info(f"Total unique links: {len(all_links)}, new (not in DB): {len(new_links)}")

    results: List[Dict[str, Any]] = []
    seen_urls: Set[str] = set()

    for i, url in enumerate(new_links, 1):
        if url in seen_urls:
            continue
        seen_urls.add(url)

        if progress_callback:
            progress_callback(current=i, total=len(new_links), url=url)

        article = parse_article_page(url)
        if article:
            results.append(article)
            logger.info(f"[{i}/{len(new_links)}] OK: {article['title'][:60]}")
        else:
            logger.debug(f"[{i}/{len(new_links)}] Skipped: {url}")

        time.sleep(INTER_REQUEST_DELAY)

    logger.info(f"Scrape complete. Parsed {len(results)} new articles.")
    return results
