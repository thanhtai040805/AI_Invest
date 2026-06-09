"""Test CafeF per-stock API"""
import httpx, json

url = "https://cafef.vn/du-lieu/Ajax/PageNew/DataHistory/GDKhoiNgoai.ashx"
headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://cafef.vn/"}

# Try different dates
for d in ["05/06/2026", "04/06/2026", "03/06/2026"]:
    params = {"Symbol": "MBB", "Exchange": "HOSE", "StartDate": d, "EndDate": d, "PageIndex": 1, "PageSize": 5}
    resp = httpx.get(url, params=params, headers=headers, timeout=15)
    data = resp.json()
    tc = data.get("Data", {}).get("TotalCount", 0)
    print(f"Date {d}: TotalCount={tc}")
    if tc > 0:
        rec = data.get("Data", {}).get("Data", [])
        if rec:
            print("  Sample:", json.dumps(rec[0], indent=2, ensure_ascii=False)[:500])
        break

# Symbol=ALL (all symbols on a date)
print("\n=== Symbol=ALL ===")
for d in ["05/06/2026"]:
    params2 = {"Symbol": "ALL", "Exchange": "HOSE", "StartDate": d, "EndDate": d, "PageIndex": 1, "PageSize": 5}
    resp2 = httpx.get(url, params=params2, headers=headers, timeout=15)
    data2 = resp2.json()
    tc2 = data2.get("Data", {}).get("TotalCount", 0)
    print(f"Date {d}: TotalCount={tc2}")
    rec2 = data2.get("Data", {}).get("Data", [])
    if rec2:
        for r in rec2[:5]:
            sym = r.get("Symbol", "?")
            rcl = r.get("RoomConLai", "?")
            dsh = r.get("DangSoHuu", "?")
            print(f"  {sym}: RoomConLai={rcl}, DangSoHuu={dsh}")
