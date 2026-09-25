from app.infrastructure.external_api.dnse.api.client import DNSEClient
from app.infrastructure.external_api.dnse.intraday_tool import DnseIntradayTool


def test_get_quotes_uses_historical_depth_endpoint_and_pagination():
    client = DNSEClient(api_key="test", api_secret="test")
    captured = {}

    def request(method, path, query=None, dry_run=False):
        captured.update(method=method, path=path, query=query, dry_run=dry_run)
        return 200, "{}"

    client._request = request
    result = client.get_quotes(
        symbol="PVT",
        board_id="G1",
        from_date=1_789_445_600,
        to_date=1_789_467_200,
        limit=100,
        order="DESC",
        next_page_token="page-2",
    )

    assert result == (200, "{}")
    assert captured == {
        "method": "GET",
        "path": "/price/PVT/quotes",
        "query": {
            "boardId": "G1",
            "from": 1_789_445_600,
            "to": 1_789_467_200,
            "limit": 100,
            "order": "DESC",
            "nextPageToken": "page-2",
        },
        "dry_run": False,
    }


def test_fetch_quotes_follows_pages_and_keeps_only_returned_records():
    pages = [
        {
            "quotes": [{
                "symbol": "PVT",
                "boardId": "G1",
                "time": "2026-09-15 14:45:03.754",
                "bid": [{"price": 23.1, "qtty": 700}],
                "offer": [{"price": 23.15, "qtty": 500}],
            }],
            "nextPageToken": "older-page",
        },
        {
            "quotes": [{
                "symbol": "PVT",
                "boardId": "G1",
                "time": "2026-09-15 14:44:59.000",
                "bid": [{"price": 23.05, "qtty": 600}],
                "offer": [{"price": 23.1, "qtty": 400}],
            }],
        },
    ]

    class Client:
        def __init__(self):
            self.calls = []

        def get_quotes(self, **kwargs):
            self.calls.append(kwargs)
            return 200, __import__("json").dumps(pages[len(self.calls) - 1])

    tool = DnseIntradayTool()
    tool._client = Client()
    tool._rate_limit = lambda: None

    quotes = tool.fetch_quotes("PVT", 100, 200, board_id="G1", limit=100)

    assert [quote["time"] for quote in quotes] == [
        "2026-09-15T14:44:59+07:00",
        "2026-09-15T14:45:03.754000+07:00",
    ]
    assert len(quotes) == 2
    assert quotes[1]["bid"] == [{"price": 23.1, "qtty": 700}]
    assert tool._client.calls[0]["order"] == "DESC"
    assert tool._client.calls[1]["next_page_token"] == "older-page"
