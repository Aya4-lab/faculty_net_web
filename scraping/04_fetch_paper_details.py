"""
04 — Scrape researchmap paper detail pages (title/authors/abstract/keywords/doi)
     with cache resume. Reads paper URLs produced by 03_fetch_researchmap_profiles.py.

Usage:
  python scraping/04_fetch_paper_details.py
"""
import json
import time
import re
import argparse
import os
from pathlib import Path

from bs4 import BeautifulSoup

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
RESEARCHMAP_DATA_DIR = PROJECT_ROOT / "data" / "raw" / "researchmap"
RESEARCHMAP_COOKIE_FILE = RESEARCHMAP_DATA_DIR / "researchmap_session_cookie.txt"
LEGACY_COOKIE_FILE = SCRIPT_DIR / "researchmap_session_cookie.txt"
RESEARCHMAP_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Code/1.131.0 Chrome/148.0.7778.280 Electron/42.7.0 Safari/537.36"
REQUEST_DELAY = 1.5


def resolve_path(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    candidate = PROJECT_ROOT / path
    if candidate.exists() or not path.exists():
        return candidate
    legacy_candidate = PROJECT_ROOT / path.name
    if legacy_candidate.exists():
        return legacy_candidate
    return path


def render_page_get_soup(url, browser, timeout=30):
    """Fetch a page exclusively via Playwright (raw requests are blocked with 403)."""
    if browser is None:
        print(f"Playwright browser is required to fetch {url}")
        return None
    try:
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=timeout * 1000)
        page.wait_for_timeout(600)
        content = page.content()
        page.close()
        return BeautifulSoup(content, "html.parser")
    except Exception as e:
        print(f"Failed to render {url} with Playwright: {e}")
        return None


def load_researchmap_cookie():
    cookie_value = os.environ.get("RESEARCHMAP_SESSION_COOKIE", "").strip()
    if cookie_value:
        return cookie_value

    for cookie_file in (RESEARCHMAP_COOKIE_FILE, LEGACY_COOKIE_FILE):
        if cookie_file.exists():
            try:
                cookie_value = cookie_file.read_text(encoding="utf-8").strip()
                if cookie_value:
                    return cookie_value
            except Exception:
                pass

    return ""


def apply_researchmap_browser_state(context):
    context.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")
    cookie_value = load_researchmap_cookie()
    if cookie_value and "=" in cookie_value:
        name, value = cookie_value.split("=", 1)
        context.add_cookies([
            {"name": name, "value": value, "domain": "researchmap.jp", "path": "/"}
        ])


def extract_table_field(soup, label_names):
    for label in label_names:
        tag = soup.find(
            lambda t: t.name in ["th", "dt", "h2", "h3", "h4", "div", "span"]
            and label in t.get_text(strip=True)
        )
        if tag:
            parent = tag.parent
            value_elem = parent.find(["td", "dd"]) or tag.find_next_sibling(["td", "dd", "div", "p"])
            if value_elem:
                return value_elem.get_text(separator=" ", strip=True)
    return ""


def authors_from_meta_text(meta_text: str) -> str:
    """Step 1 meta_text is typically: title | authors | journal..."""
    parts = [p.strip() for p in (meta_text or "").split("|") if p.strip()]
    if len(parts) >= 2:
        return parts[1]
    return ""


def is_bad_authors(authors: str) -> bool:
    """Detect bogus author strings scraped from detail pages (volume nos, BibTeX, etc.)."""
    t = (authors or "").strip()
    if not t:
        return True
    if re.fullmatch(r"\d+", t):
        return True
    if re.search(r"bibtex|^\s*ris\s*$", t, re.I):
        return True
    if "BibTeX" in t or t.upper() == "RIS":
        return True
    return False


def scrape_paper_detail(paper_url, lang="ja", render_browser=None, step1_title="", step1_meta_text=""):
    """
    Fetch abstract/keywords/doi via Playwright.
    title / authors are taken from Step 1 (detail-page h1/authors are unreliable).
    """
    target_url = f"{paper_url}?lang={lang}" if "?" not in paper_url else f"{paper_url}&lang={lang}"
    soup = render_page_get_soup(target_url, render_browser)
    if not soup:
        return None

    title = (step1_title or "").strip()
    authors = authors_from_meta_text(step1_meta_text)

    # Optional page authors only if Step 1 lacked an author segment and page value looks sane
    if not authors:
        page_authors = extract_table_field(soup, ["著者", "Authors", "Author"])
        if page_authors and not is_bad_authors(page_authors):
            authors = page_authors

    abstract = extract_table_field(soup, ["概要", "抄録", "Abstract", "Description"])
    if not abstract:
        desc = soup.find(class_=re.compile(r"description|abstract|outline", re.I))
        if desc:
            abstract = desc.get_text(separator=" ", strip=True)

    keywords_raw = extract_table_field(soup, ["キーワード", "Keywords", "Keyword"])
    keywords = [k.strip() for k in re.split(r"[,;、\n]+", keywords_raw) if k.strip()] if keywords_raw else []

    doi_tag = soup.find("a", href=re.compile(r"doi\.org/"))
    doi = doi_tag["href"] if doi_tag else ""

    return {
        "paper_url": paper_url,
        "lang": lang,
        "title": title,
        "authors": authors,
        "abstract": abstract or "",
        "keywords": keywords,
        "doi": doi or "",
    }


def load_paper_cache(out_path: Path) -> dict:
    if not out_path.exists():
        return {}
    try:
        with open(out_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            cache = {}
            for item in data:
                url = item.get("paper_url") if isinstance(item, dict) else None
                if url:
                    cache[url] = item
            return cache
    except Exception as e:
        print(f"Warning: failed to load cache {out_path}: {e}")
    return {}


def save_paper_cache(out_path: Path, paper_cache: dict):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(paper_cache, f, ensure_ascii=False, indent=2)


def collect_paper_meta_from_step1(faculty_list):
    """Map paper_url -> {title, meta_text} from Step 1 faculty JSON."""
    papers = {}
    for fac in faculty_list:
        for p in fac.get("papers", []) or []:
            url = p.get("paper_url")
            if not url or url in papers:
                continue
            papers[url] = {
                "title": (p.get("title") or "").strip(),
                "meta_text": p.get("meta_text") or "",
            }
    return papers


def apply_step1_title_authors(entry: dict, step1_meta: dict) -> dict:
    """Overlay reliable title/authors from Step 1 onto a detail record."""
    updated = dict(entry or {})
    title = (step1_meta.get("title") or "").strip()
    authors = authors_from_meta_text(step1_meta.get("meta_text") or "")
    if title:
        updated["title"] = title
    if authors:
        updated["authors"] = authors
    elif is_bad_authors(updated.get("authors", "")):
        updated["authors"] = ""
    return updated


def process_language_details(input_file, output_file, lang="ja", render_browser=None, delay=REQUEST_DELAY):
    in_path = resolve_path(input_file)
    out_path = resolve_path(output_file)

    if not in_path.exists():
        print(f"Skipping: {in_path} does not exist.")
        return

    with open(in_path, "r", encoding="utf-8") as f:
        faculty_list = json.load(f)

    paper_meta = collect_paper_meta_from_step1(faculty_list)
    unique_urls = list(paper_meta.keys())
    paper_cache = load_paper_cache(out_path)

    # Fix title/authors on already-cached entries without re-fetching
    for p_url, meta in paper_meta.items():
        if p_url in paper_cache:
            paper_cache[p_url] = apply_step1_title_authors(paper_cache[p_url], meta)

    print(f"\nProcessing [{lang.upper()}] - {len(unique_urls)} unique papers ({len(paper_cache)} cached)")
    new_scrapes = 0

    for idx, p_url in enumerate(unique_urls, 1):
        cached = paper_cache.get(p_url)
        # Skip only if already scraped (has been through detail fetch). Use doi/abstract/keywords
        # presence OR a non-empty title that came from step1 after overlay — prefer:
        # skip when cache exists AND was previously scraped (has lang key and we already overlaid).
        # Re-scrape only when missing from cache.
        if cached is not None and ("abstract" in cached or "doi" in cached):
            # Already have a detail record; title/authors were overlaid above.
            continue

        meta = paper_meta.get(p_url) or {}
        print(f"[{idx}/{len(unique_urls)}] ({lang}) Scraping: {p_url}")
        detail = scrape_paper_detail(
            p_url,
            lang=lang,
            render_browser=render_browser,
            step1_title=meta.get("title", ""),
            step1_meta_text=meta.get("meta_text", ""),
        )
        if detail:
            paper_cache[p_url] = detail
            new_scrapes += 1

            if new_scrapes % 5 == 0:
                save_paper_cache(out_path, paper_cache)
                print(f"  Checkpoint saved ({new_scrapes} new) -> {out_path}")

        if delay and delay > 0:
            time.sleep(delay)

    save_paper_cache(out_path, paper_cache)
    print(f"Saved: {out_path} (new scrapes this run: {new_scrapes})")


def run_step2(input_ja, input_en, output_ja, output_en, delay=REQUEST_DELAY):
    playwright_controller = None
    browser = None
    try:
        from playwright.sync_api import sync_playwright
        playwright_controller = sync_playwright().start()
        browser = playwright_controller.chromium.launch(headless=True).new_context(
            user_agent=RESEARCHMAP_USER_AGENT,
            locale="ja-JP",
        )
        apply_researchmap_browser_state(browser)
        print("Playwright browser context started (required to avoid 403).")

        process_language_details(
            input_ja, output_ja, lang="ja", render_browser=browser, delay=delay
        )
        process_language_details(
            input_en, output_en, lang="en", render_browser=browser, delay=delay
        )
    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        if playwright_controller:
            try:
                playwright_controller.stop()
            except Exception:
                pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="04: fetch paper details (abstract etc.) from 03 profile paper URLs."
    )
    parser.add_argument("--input-ja", default="data/raw/researchmap/remp_yugo_faculty_ja.json")
    parser.add_argument("--input-en", default="data/raw/researchmap/remp_yugo_faculty_en.json")
    parser.add_argument("--output-ja", default="data/raw/researchmap/remp_yugo_paper_details_ja.json")
    parser.add_argument("--output-en", default="data/raw/researchmap/remp_yugo_paper_details_en.json")
    parser.add_argument("--delay", type=float, default=REQUEST_DELAY, help="Delay between paper scrapes (seconds)")
    parser.add_argument("--render", action="store_true", help="Deprecated: Playwright is always used")
    args = parser.parse_args()

    run_step2(
        args.input_ja,
        args.input_en,
        args.output_ja,
        args.output_en,
        delay=args.delay,
    )
