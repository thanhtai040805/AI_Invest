import httpx, json

base = "https://api-ai.alphastock.vn"
headers = {"User-Agent": "Mozilla/5.0 Chrome/120.0.0.0", "Accept": "application/json", "Referer": "https://ai.alphastock.vn/"}
c = httpx.Client(verify=False, timeout=30.0, headers=headers)

for sym in ["VMD", "HPG"]:
    resp = c.get(f"{base}/api/v1/financials/report-workspace?symbol={sym}&quarter_limit=24&annual_limit=15")
    data = resp.json()
    print(f"=== {data['company']['company_name']} ({sym}) ===")

    inc = data["statements"]["income_statement"]["quarter"]
    rows = inc["data"]
    mj = rows[0]["metrics_json"]
    print(f"\nIncome Statement ({rows[0]['period_label']}) — {len(mj)} non-null metrics:")
    for k, v in sorted(mj.items()):
        if v is not None:
            print(f"  {k}: {v:,.0f}")

    labels = [r["period_label"] for r in rows]
    print(f"\nPeriods ({len(labels)}): {labels[0]} ... {labels[-1]}")
    print()

c.close()
