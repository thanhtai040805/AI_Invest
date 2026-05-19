#!/usr/bin/env python3
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from app.config import get_dnse_client


def main():
    client = get_dnse_client()

    status, body = client.get_trades(symbol="GAS", board_id="G1", from_date=1773282637, to_date=1773289837, limit = 100, order = "DESC", next_page_token=None, dry_run=False)
    print(status, body)


if __name__ == "__main__":
    main()
