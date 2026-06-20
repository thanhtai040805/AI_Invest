"""
cafef_listing_crawl.py (scripts) — CafeF news listing + content scraper.

Mirrors app/dataflows/vendors/vn/cafef_listing_crawl.py with same logic.
Uses news_utils from the app module for extraction + DB ops.
"""
import asyncio, logging, os, sys, re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.infrastructure.vendors.vn.cafef_listing_crawl import refresh_listing

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

if __name__ == "__main__":
    args = sys.argv[1:]
    pages = 1
    deep = True
    i = 0
    while i < len(args):
        if args[i] == "--pages" and i + 1 < len(args):
            pages = int(args[i + 1])
            i += 2
        elif args[i] == "--no-deep":
            deep = False
            i += 1
        elif args[i] == "--limit-articles" and i + 1 < len(args):
            pass  # kept for compat but unused
            i += 2
        else:
            print(f"Usage: python scripts/cafef_listing_crawl.py [--pages N] [--no-deep]")
            sys.exit(1)

    result = refresh_listing(max_pages=pages, deep_crawl=deep)
    print(result)
