"""Find CafeF company info API"""
import httpx, re, json

headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://cafef.vn/"}

# Try common CafeF info API patterns
apis = [
    ("/du-lieu/Ajax/PageNew/CompanyInfo/CompanyInfo.ashx?Symbol=MBB", "CompanyInfo"),
    ("/du-lieu/Ajax/PageNew/BasicInfo.ashx?Symbol=MBB", "BasicInfo"),
    ("/du-lieu/Ajax/PageNew/DoanhNghiep/ThongTinCoBan.ashx?Symbol=MBB", "ThongTinCoBan"),
    ("/du-lieu/Ajax/PageNew/EnterpriseInfo.ashx?Symbol=MBB&Exchange=HOSE", "EnterpriseInfo"),
    ("/du-lieu/Ajax/PageNew/DataHistory/Overview.ashx?Symbol=MBB", "Overview"),
    ("https://cafef.vn/du-lieu/Ajax/PageNew/DoanhNghiep/ThongTinCoBan.ashx?Symbol=MBB", "ThongTinCoBan2"),
]

base = "https://cafef.vn"
for path, name in apis:
    url = path if path.startswith("http") else base + path
    try:
        resp = httpx.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            txt = resp.text[:300]
            print(f"[{resp.status_code}] {name}: {txt}")
        else:
            print(f"[{resp.status_code}] {name}: HTTP {resp.status_code}")
    except Exception as e:
        print(f"[ERR] {name}: {e}")

# Also check for shares_outstanding in existing data
print("\n--- Checking for shares_outstanding in financial_statements ---")
# Try the 'ratios' type financial_statements
url2 = "https://cafef.vn/du-lieu/Ajax/PageNew/DataHistory/GDKhoiNgoai.ashx?Symbol=MBB&Exchange=HOSE&StartDate=05/06/2026&EndDate=05/06/2026&PageIndex=1&PageSize=1"
resp2 = httpx.get(url2, headers=headers, timeout=10)
if resp2.status_code == 200:
    d2 = resp2.json()
    rec = d2.get("Data", {}).get("Data", [])
    if rec:
        r = rec[0]
        print(f"GDKhoiNgoai fields: {list(r.keys())}")
        print(f"RoomConLai={r.get('RoomConLai')}, DangSoHuu={r.get('DangSoHuu')}")
