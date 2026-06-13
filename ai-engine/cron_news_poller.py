"""Cron runner: crawl CafeF listing → deep crawl new articles.

Runs every 15 minutes. Fast path: 1 listing page (0.1s) + deep crawl (2s per article).
"""
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("cron.news_poller")


def main():
    # Step 1: Fetch listing
    logger.info("Step 1: Fetch CafeF listing...")
    from app.dataflows.vendors.vn.cafef_listing_crawl import refresh_listing
    listing_result = refresh_listing(max_pages=1)
    logger.info("  Result: %s", listing_result)

    inserted = listing_result.get("inserted", 0)

    # Step 2: Deep crawl HTML + PDF for new articles
    if inserted > 0:
        logger.info("Step 2: Deep crawl %d new articles...", inserted)
        from app.dataflows.vendors.vn.deep_crawl_news import refresh_deep_crawl
        crawl_result = refresh_deep_crawl(limit=inserted)
        logger.info("  Result: %s", crawl_result)
    else:
        logger.info("Step 2: No new articles, skip deep crawl.")

    logger.info("Cron cycle complete.")


if __name__ == "__main__":
    main()
