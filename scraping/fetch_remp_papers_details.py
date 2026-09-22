# step2_scrape_paper_details.py
import json
import time
import re
import argparse
from pathlib import Path
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def resolve_path(path_str: str) -> Path:
    p = Path(path_str)
    return p if p.is_absolute() else Path(__file__).resolve().parent.parent / p

def get_soup(url, session=None, timeout=15):
    s = session or requests
    try:
        resp = s.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print(f"Failed to GET {url}: {e}")
        return None

def extract_table_field(soup, label_names):
    for label in label_names:
        tag = soup.find(lambda t: t.name in ["th", "dt", "h2", "h3", "h4", "div", "span"] and label in t.get_text(strip=True))
        if tag:
            parent = tag.parent
            value_elem = parent.find(["td", "dd"]) or tag.find_next_sibling(["td", "dd", "div", "p"])
            if value_elem:
                return value_elem.get_text(separator=" ", strip=True)
    return ""

def scrape_paper_detail(paper_url, lang="ja", session=None):
    target_url = f"{paper_url}?lang={lang}" if "?" not in paper_url else f"{paper_url}&lang={lang}"
    soup = get_soup(target_url, session=session)
    if not soup:
        return None

    title_tag = soup.find("h1") or soup.find(class_=re.compile(r"paper-title|title", re.I))
    title = title_tag.get_text(strip=True) if title_tag else ""

    authors = extract_table_field(soup, ["著者", "Authors", "Author"])
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
        "abstract": abstract,
        "keywords": keywords,
        "doi": doi
    }

def process_language_details(input_file, output_file, lang="ja", session=None, delay=1.0):
    in_path = resolve_path(input_file)
    out_path = resolve_path(output_file)

    if not in_path.exists():
        print(f"Skipping: {in_path} does not exist.")
        return

    with open(in_path, "r", encoding="utf-8") as f:
        faculty_list = json.load(f)

    paper_cache = {}
    if out_path.exists():
        try:
            with open(out_path, "r", encoding="utf-8") as f:
                paper_cache = json.load(f)
        except Exception:
            pass

    unique_urls = []
    for fac in faculty_list:
        for p in fac.get("papers", []):
            url = p.get("paper_url")
            if url and url not in unique_urls:
                unique_urls.append(url)

    print(f"\nProcessing [{lang.upper()}] - {len(unique_urls)} unique papers")
    new_scrapes = 0

    for idx, p_url in enumerate(unique_urls, 1):
        if p_url in paper_cache and paper_cache[p_url].get("title"):
            continue

        print(f"[{idx}/{len(unique_urls)}] ({lang}) Scraping: {p_url}")
        detail = scrape_paper_detail(p_url, lang=lang, session=session)
        if detail:
            paper_cache[p_url] = detail
            new_scrapes += 1

        if new_scrapes > 0 and new_scrapes % 5 == 0:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(paper_cache, f, ensure_ascii=False, indent=2)

        time.sleep(delay)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(paper_cache, f, ensure_ascii=False, indent=2)
    print(f"Saved: {out_path}")

def run_step2(input_ja, input_en, output_ja, output_en, delay=1.0):
    session = requests.Session()
    session.headers.update(HEADERS)

    # 日本語版の抽出・保存
    process_language_details(input_ja, output_ja, lang="ja", session=session, delay=delay)
    # 英語版の抽出・保存
    process_language_details(input_en, output_en, lang="en", session=session, delay=delay)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-ja", default="data/raw/researchmap/remp_yugo_faculty_ja.json")
    parser.add_argument("--input-en", default="data/raw/researchmap/remp_yugo_faculty_en.json")
    parser.add_argument("--output-ja", default="data/raw/researchmap/remp_yugo_paper_details_ja.json")
    parser.add_argument("--output-en", default="data/raw/researchmap/remp_yugo_paper_details_en.json")
    parser.add_argument("--delay", type=float, default=1.0)
    args = parser.parse_args()

    run_step2(args.input_ja, args.input_en, args.output_ja, args.output_en, delay=args.delay)