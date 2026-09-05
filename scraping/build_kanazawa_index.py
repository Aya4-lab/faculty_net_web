import json
import time
import re
import argparse
from urllib.parse import quote_plus
from pathlib import Path

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

NOISE_TOKENS = {
    "日本語 | English", "新規登録", "ログイン", "ホーム", "マイポータルへ", "マイポータル",
    "研究ブログ", "資料公開", "English", "20件", "50件", "100件", "Login", "Register", "Home"
}

def is_noise(text: str) -> bool:
    t = text.strip()
    if not t or t in NOISE_TOKENS or len(t) <= 1:
        return True
    return False

def get_soup(url, session=None, timeout=15):
    s = session or requests
    try:
        resp = s.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print(f"Failed to GET {url}: {e}")
        return None

def parse_search_results(soup):
    results = []
    if not soup:
        return results

    # 各研究者の検索結果カード・行を探索
    items = soup.select(".media, .rm-search-result-item, .list-group-item, article")
    if not items:
        # フォールバック: プロフィールリンクを含むdivを探す
        items = [a.find_parent("div") for a in soup.find_all("a", href=True) if re.match(r'^(/|https?://researchmap\.jp/)[^/?#]+$', a["href"])]
        items = [it for it in items if it]

    seen_urls = set()
    for item in items:
        link_tag = None
        for a in item.find_all("a", href=True):
            href = a["href"].strip()
            if not re.match(r'^(/|https?://researchmap\.jp/)[^/?#]+$', href):
                continue
            if any(p in href for p in ["/search", "/static", "/assets", "/help", "/terms", "/privacy", "/regist", "researchers?"]):
                continue
            link_tag = a
            break

        if not link_tag:
            continue

        raw_href = link_tag["href"].strip()
        profile_url = raw_href if raw_href.startswith("http") else f"https://researchmap.jp{raw_href}"
        profile_base = profile_url.split("?")[0].rstrip("/")

        if profile_base in seen_urls:
            continue
        seen_urls.add(profile_base)

        name = link_tag.get_text(separator=" ", strip=True)
        if is_noise(name):
            continue

        # 所属・職位テキストの取得
        affil = ""
        affil_tag = item.find(class_=re.compile(r"affiliation|organization|detail|text-muted", re.I))
        if affil_tag:
            affil = affil_tag.get_text(separator=" ", strip=True)

        results.append({
            "name": name,
            "affiliation": affil,
            "profile_url": profile_base
        })

    return results

def build_index(query="金沢大学", out_file="kanazawa_researchers_index.json", max_pages=150, delay=1.0):
    session = requests.Session()
    all_researchers = []
    seen_urls = set()

    print(f"Starting index build for query: '{query}'")
    for page in range(max_pages):
        start = page * 20
        url = f"https://researchmap.jp/researchers?q={quote_plus(query)}&start={start}"
        print(f"Fetching page {page + 1}: {url}")

        soup = get_soup(url, session=session)
        items = parse_search_results(soup)

        if not items:
            print("No items found. Reached the end of results.")
            break

        new_count = 0
        for it in items:
            if it["profile_url"] not in seen_urls:
                seen_urls.add(it["profile_url"])
                all_researchers.append(it)
                new_count += 1

        print(f"  -> Extracted {len(items)} items ({new_count} new). Total collected: {len(all_researchers)}")
        if len(items) < 20:
            print("Last page reached.")
            break

        time.sleep(delay)

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(all_researchers, f, ensure_ascii=False, indent=2)
    print(f"\nIndex saved to {out_file} (Total: {len(all_researchers)} researchers)")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build index of researchers for an organization.")
    parser.add_argument("--query", default="金沢大学", help="Search keyword")
    parser.add_argument("--out", default="kanazawa_researchers_index.json", help="Output index JSON file")
    parser.add_argument("--max-pages", type=int, default=120, help="Max search pages to scan (120 pages ≈ 2400 items)")
    parser.add_argument("--delay", type=float, default=1.0, help="Request interval in seconds")
    args = parser.parse_args()

    build_index(query=args.query, out_file=args.out, max_pages=args.max_pages, delay=args.delay)