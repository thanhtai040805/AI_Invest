"""
vietstock_news_crawl.py (scripts) — Vietstock news scraper.

Mirrors app/dataflows/vendors/vn/vietstock_news_crawl.py with same logic.
Uses news_utils from the app module for extraction + DB ops.
"""
import logging, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.infrastructure.vendors.vn.vietstock_news_crawl import refresh_listing

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

if __name__ == "__main__":
    args = sys.argv[1:]
    channels = None
    deep = True
    i = 0
    while i < len(args):
        if args[i] == "--channels" and i + 1 < len(args):
            channels = [int(x) for x in args[i + 1].split(",")]
            i += 2
        elif args[i] == "--no-deep":
            deep = False
            i += 1
        else:
            print(f"Usage: python scripts/vietstock_news_crawl.py [--channels 144,733] [--no-deep]")
            sys.exit(1)

    result = refresh_listing(channel_ids=channels, deep_crawl=deep)
    print(result)
