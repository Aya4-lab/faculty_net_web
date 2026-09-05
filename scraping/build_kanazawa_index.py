import json
import time
import re
import argparse
import os
from urllib.parse import quote_plus
from pathlib import Path

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

SCRIPT_DIR = Path(__file__).resolve().parent
RESEARCHMAP_COOKIE_FILE = SCRIPT_DIR / "researchmap_session_cookie.txt"
RESEARCHMAP_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Code/1.131.0 Chrome/148.0.7778.280 Electron/42.7.0 Safari/537.36"

NOISE_TOKENS = {
    "日本語 | English", "新規登録", "ログイン", "ホーム", "マイポータルへ", "マイポータル",
    "研究ブログ", "資料公開", "English", "20件", "50件", "100件", "Login", "Register", "Home",
    "クリア", "一致した業績を検索", "研究者検索"
}

def is_noise(text: str) -> bool:
    t = text.strip()
    if not t or t in NOISE_TOKENS or len(t) <= 1:
        return True
    return False


def load_researchmap_cookie():
    cookie_value = os.environ.get("RESEARCHMAP_SESSION_COOKIE", "").strip()
    if cookie_value:
        return cookie_value

    if RESEARCHMAP_COOKIE_FILE.exists():
        try:
            cookie_value = RESEARCHMAP_COOKIE_FILE.read_text(encoding="utf-8").strip()
            if cookie_value:
                return cookie_value
        except Exception:
            pass

    return ""

def extract_cards_with_soup(page_html):
    soup = BeautifulSoup(page_html, "html.parser")
    results = []
    seen_urls = set()

    exclude_keywords = [
        "/search", "/static", "/assets", "/help", "/terms", "/privacy", 
        "/regist", "/researchers", "/achievements", "/new_accounts", "/auth",
        "researchers?", "/login", "/logout", "/languages", "javascript:"
    ]

    # 1. 検索結果カードのまとまりを探索（リストアイテムやカード構造）
    cards = soup.select("ul.search-results > li, .rm-search-result-item, .list-group-item, li.media, div.media")
    
    # セレクタで拾えなかった場合のフォールバック: 全アンカーから抽出
    if not cards:
        cards = [soup]

    for card in cards:
        for a in card.find_all("a", href=True):
            href = a["href"].strip()
            
            # システムリンクやページネーションを除外
            if any(k in href for k in exclude_keywords) or href in ("/", "#"):
                continue

            # プロフィールURLの判定（クエリパラメータ等を除去してベースURLを検証）
            clean_href = href.split("?")[0].split("#")[0].rstrip("/")

            if clean_href in {"/researchers", "/achievements", "/new_accounts", "/auth"}:
                continue
            
            # /ユーザーID または https://researchmap.jp/ユーザーID に合致するか
            match_relative = re.match(r"^/[A-Za-z0-9_\-\.]+$", clean_href)
            match_absolute = re.match(r"^https?://researchmap\.jp/[A-Za-z0-9_\-\.]+$", clean_href)

            if not (match_relative or match_absolute):
                continue

            name = a.get_text(separator=" ", strip=True)
            # 名前のテキストが空、あるいは数字・ノイズの場合はスキップ
            if is_noise(name) or re.match(r"^\d+$", name):
                continue

            base_url = clean_href if clean_href.startswith("http") else f"https://researchmap.jp{clean_href}"

            if base_url in seen_urls:
                continue
            seen_urls.add(base_url)

            # 所属情報の探索
            parent_container = a.find_parent(["li", "div", "article"]) or card
            affil = ""
            if parent_container:
                for line in parent_container.get_text(separator="\n", strip=True).splitlines():
                    line_str = line.strip()
                    if "金沢大学" in line_str and line_str != name:
                        affil = line_str
                        break

            results.append({
                "name": name,
                "affiliation": affil,
                "profile_url": base_url
            })

    return results

def build_index_via_browser(query="金沢大学", out_file="kanazawa_researchers_index.json", max_pages=None, delay=1.5, page_size=20):
    all_researchers = []
    seen_urls = set()
    page_idx = 0
    cookie_value = load_researchmap_cookie()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(
            user_agent=RESEARCHMAP_USER_AGENT,
            locale="ja-JP"
        )
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")
        if cookie_value:
            context.add_cookies([
                {
                    "name": cookie_value.split("=", 1)[0],
                    "value": cookie_value.split("=", 1)[1] if "=" in cookie_value else "",
                    "domain": "researchmap.jp",
                    "path": "/",
                }
            ])
        page = context.new_page()

        while True:
            if max_pages is not None and page_idx >= max_pages:
                break

            start = page_idx * page_size
            target_url = f"https://researchmap.jp/researchers?q={quote_plus(query)}&start={start}"
            page_label = f"{page_idx + 1}" if max_pages is None else f"{page_idx + 1}/{max_pages}"
            print(f"\n[{page_label}] Accessing: {target_url}")

            try:
                page.goto(target_url, wait_until="domcontentloaded", timeout=30000)

                # 結果カードのリンクが現れるまで待機
                try:
                    page.wait_for_selector('a[href*="/"]', timeout=10000)
                except Exception:
                    pass

                # レンダリング安定のための待機
                page.wait_for_timeout(2500)
                html = page.content()

            except Exception as e:
                print(f"  Load error: {e}")
                page_idx += 1
                continue

            items = extract_cards_with_soup(html)
            new_count = 0
            for it in items:
                if it["profile_url"] not in seen_urls:
                    seen_urls.add(it["profile_url"])
                    all_researchers.append(it)
                    new_count += 1

            print(f"  -> Extracted {len(items)} items on page ({new_count} new). Total collected: {len(all_researchers)}")

            if len(items) == 0:
                with open("debug_page_source.html", "w", encoding="utf-8") as df:
                    df.write(html)
                print("  No researchers detected. Saved HTML to 'debug_page_source.html'. Stopping.")
                break

            if len(items) < page_size:
                print("  Last page reached; stopping pagination.")
                break

            time.sleep(delay)
            page_idx += 1

        browser.close()

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(all_researchers, f, ensure_ascii=False, indent=2)

    print(f"\nCompleted! Saved {len(all_researchers)} researchers to '{out_file}'.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", default="金沢大学")
    parser.add_argument("--out", default="kanazawa_researchers_index.json")
    parser.add_argument("--pages", "--max-pages", dest="pages", type=int, default=0, help="Optional safety cap; 0 means crawl until the last page.")
    parser.add_argument("--delay", type=float, default=1.5)
    args = parser.parse_args()

    build_index_via_browser(
        query=args.query,
        out_file=args.out,
        max_pages=args.pages if args.pages and args.pages > 0 else None,
        delay=args.delay,
    )