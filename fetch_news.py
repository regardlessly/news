#!/usr/bin/env python3
"""
App 1 — CNA News Fetcher (batch script)

Usage:
    python fetch_news.py

Schedule with cron (daily at 7 AM SGT = 23:00 UTC):
    0 23 * * * cd /path/to/project && .venv/bin/python fetch_news.py >> fetch.log 2>&1
"""
import logging
import sys
import time
from datetime import datetime

import database
import scraper
import summariser

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("fetch.log"),
    ],
)
logger = logging.getLogger(__name__)


def main():
    start = time.time()
    logger.info("=" * 60)
    logger.info(f"CNA News Fetch started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    # 1. Initialise DB
    logger.info("Initialising database...")
    database.init_db()

    # 2. Prune articles older than 7 days
    deleted = database.delete_old_articles(days=7)
    logger.info(f"Pruned {deleted} articles older than 7 days")

    # 3. Get existing URLs to avoid re-scraping
    existing_urls = database.get_all_urls()
    logger.info(f"Existing articles in DB: {len(existing_urls)}")

    # 4. Scrape CNA
    logger.info("Starting CNA scrape...")

    def progress(current, total, url):
        if current % 10 == 0 or current == total:
            logger.info(f"  Progress: {current}/{total} articles processed")

    raw_articles = scraper.scrape_all(
        existing_urls=existing_urls,
        progress_callback=progress,
    )
    logger.info(f"Scraped {len(raw_articles)} new articles")

    if not raw_articles:
        logger.info("No new articles found. Exiting.")
        elapsed = time.time() - start
        logger.info(f"Done in {elapsed:.1f}s")
        return

    # 5. Insert into DB
    logger.info("Inserting articles into database...")
    inserted: list = []
    skipped = 0

    for art in raw_articles:
        try:
            article_id = database.insert_article(
                url=art["url"],
                title=art["title"],
                section=art["section"],
                full_text=art.get("full_text", ""),
                published_at=art.get("published_at"),
            )
            inserted.append({
                "id": article_id,
                "title": art["title"],
                "full_text": art.get("full_text", ""),
            })
        except Exception as e:
            # Most likely a duplicate URL (UNIQUE constraint)
            skipped += 1
            logger.debug(f"Skipped (duplicate or error): {art.get('url')} — {e}")

    logger.info(f"Inserted {len(inserted)} articles, skipped {skipped} duplicates")

    # 6. Summarise new articles
    if inserted:
        logger.info(f"Summarising {len(inserted)} articles with DeepSeek...")
        stats = summariser.summarise_batch(
            articles=inserted,
            update_fn=database.update_summary,
            delay_seconds=0.5,
        )
        logger.info(
            f"Summarisation complete: {stats['success']} succeeded, "
            f"{stats['failed']} failed"
        )

    # 7. Final summary
    elapsed = time.time() - start
    total_in_db = database.get_article_count(days=7)
    logger.info("=" * 60)
    logger.info(
        f"DONE in {elapsed:.1f}s | "
        f"New: {len(inserted)} | "
        f"Skipped: {skipped} | "
        f"Total in DB (7d): {total_in_db}"
    )
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
