"""Check if old CafeF article URLs (from 2023 sitemap) are still accessible."""
import httpx, re, random

r = httpx.get(
    "https://cafef.vn/sitemaps/sitemaps-2023-1-1-5.xml",
    follow_redirects=True,
    timeout=30,
    headers={"User-Agent": "Mozilla/5.0"},
)
urls = re.findall(r"<loc>(.*?)</loc>", r.text)
print(f"2023 sitemap: {len(urls)} URLs")

for u in random.sample(urls, min(5, len(urls))):
    resp = httpx.get(u, follow_redirects=True, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
    has = resp.status_code == 200 and "KenhF_Content_News3" in resp.text or "detail-content" in resp.text
    print(f"  {resp.status_code} content={has}  {u.split('/')[-1][:50]}")
