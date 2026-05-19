#!/usr/bin/env python3
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from app.config import get_dnse_client


def main():
    client = get_dnse_client()

    status, body = client.get_security_definition(symbol="HPG", board_id=None, dry_run=False)
    print(status, body)


if __name__ == "__main__":
    main()
