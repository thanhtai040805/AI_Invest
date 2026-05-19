#!/usr/bin/env python3
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from app.config import get_dnse_client


def main():
    client = get_dnse_client()

    status, body = client.get_ohlc(
        bar_type="STOCK",
        query={
            "symbol": "HPG",
            "resolution": "1",
            "from": 1735689600,
            "to": 1735776000,
        },
        dry_run=False,
    )
    print(status, body)


if __name__ == "__main__":
    main()
