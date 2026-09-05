import json
import time
import re
import argparse
from urllib.parse import quote_plus
import os
from pathlib import Path

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent


def resolve_path(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    candidate = PROJECT_ROOT / path
    if candidate.exists() or not path.exists():
        return candidate
    return path

NOISE_TOKENS = {
    "日本語 | English", "新規登録", "ログイン", "ホーム", "マイポータルへ", "マイポータル",
    "研究ブログ", "資料公開", "English", "20件", "50件", "100件",
    "Login", "Register", "Home", "My Portal", "Japanese | English"
}

COMMON_HEADER_LABELS = {
    "研究分野", "経歴", "学歴", "論文", "研究キーワード", "所属学会", "所属学協会",
    "受賞", "担当経験のある科目(授業)", "共同研究・競争的資金等の研究課題",
    "委員歴", "MISC", "書籍等出版物", "講演・口頭発表等", "Works(作品等)", "産業財産権", "ホーム",
    "社会貢献活動", "メディア報道", "その他",
    "Research Areas", "Research Experience", "Education", "Published Papers", 
    "Research Keywords", "Association Memberships", "Awards", "Works", 
    "Presentations", "Misc", "Media Coverage", "Social Contributions", "Others"
}

NOISE_REGEX = [
    re.compile(r"^\d+件$"),
    re.compile(r"^\d+$"),
    re.compile(r"^\{\{"),
]

def is_noise(text: str) -> bool:
    t = text.strip()
    if not t:
        return True
    if t in NOISE_TOKENS or t in COMMON_HEADER_LABELS:
        return True
    for rx in NOISE_REGEX:
        if rx.search(t):
            return True
    if len(t) <= 1:
        return True
    if any(tok in t for tok in ["マイポータル", "新規登録", "ログイン", "日本語", "資料公開", "Login", "Register", "My Portal"]):
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

def render_page_get_soup(url, browser, timeout=30, save_html_path=None):
    try:
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=timeout*1000)
        page.wait_for_timeout(500)
        content = page.content()
        if save_html_path:
            try:
                os.makedirs(os.path.dirname(save_html_path), exist_ok=True)
                with open(save_html_path, "w", encoding="utf-8") as fh:
                    fh.write(content)
            except Exception:
                pass
        page.close()
        return BeautifulSoup(content, "html.parser")
    except Exception as e:
        print(f"Failed to render {url} with Playwright: {e}")
        return None

def find_profile_link_from_search(soup, target_name):
    if not soup:
        return None

    target_clean = re.sub(r"（.*?）|\(.*?\)", "", target_name)
    target_norm = re.sub(r"\s+", "", target_clean).strip()

    def is_profile_href(href):
        if not href or href in ("/", "#") or href.startswith("javascript:"):
            return False
        if "researchers?" in href or "search_word" in href:
            return False
        if href.startswith("/"):
            non_profile = ["/search", "/static", "/assets", "/help", "/terms", "/privacy"]
            if any(href.startswith(p) for p in non_profile):
                return False
            return True
        if re.match(r'^https?://researchmap\.jp/.+$', href):
            return True
        return False

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not is_profile_href(href):
            continue
        text = a.get_text(separator=" ", strip=True)
        if not text:
            continue
        text_norm = re.sub(r"\s+", "", text).strip()
        if text_norm == target_norm or target_norm in text_norm:
            return href if href.startswith("http") else f"https://researchmap.jp{href}"

    return None

def extract_section_by_heading(soup, heading_keywords):
    results = []
    for tag in soup.find_all(re.compile(r'^h[1-6]$')):
        txt = tag.get_text(strip=True)
        if not txt:
            continue
        for hk in heading_keywords:
            if hk in txt:
                parent = tag.parent
                for sib in tag.find_next_siblings(limit=6):
                    lis = sib.find_all("li")
                    if lis:
                        for li in lis:
                            t = li.get_text(separator=" ", strip=True)
                            if t and not is_noise(t):
                                results.append(t)
                        if results:
                            return results
                    anchors = sib.find_all("a")
                    if anchors:
                        for a in anchors:
                            t = a.get_text(strip=True)
                            if t and not is_noise(t) and t not in heading_keywords:
                                results.append(t)
                        if results:
                            return results
                    ptexts = [p.get_text(strip=True) for p in sib.find_all("p") if p.get_text(strip=True)]
                    if ptexts:
                        return [pt for pt in ptexts if not is_noise(pt)]

                lis = parent.find_all("li")
                for li in lis:
                    t = li.get_text(separator=" ", strip=True)
                    if t and not is_noise(t):
                        results.append(t)
                if results:
                    return results
    return []

def extract_items_from_url(url, session=None, per_page=100, delay=1.0, render_browser=None):
    s = session or requests
    items = []
    seen = set()
    start = 0
    while True:
        page_url = f"{url}&start={start}" if "?" in url else f"{url}?start={start}"
        soup = render_page_get_soup(page_url, render_browser) if render_browser else get_soup(page_url, session=s)
        if not soup:
            break

        nodes = soup.find_all("li")
        count_nodes = len(nodes)
        for n in nodes:
            text = n.get_text(separator=" ", strip=True)
            if text and text not in seen and not is_noise(text):
                seen.add(text)
                items.append(text)

        if count_nodes < per_page:
            break
        start += per_page
        time.sleep(delay)

    return items

def extract_coauthors_from_soup(soup):
    results = []
    if not soup:
        return results
    outer = soup.find(id="rm-coauthors-outer") or soup.find(class_="rm-side-block-avatar-list")
    if not outer:
        return results

    thumbs = outer.find_all("div", class_=lambda c: c and ("thumbnail" in c.split() or "col-sm" in c))
    for t in thumbs:
        try:
            a = t.find("a", href=True)
            img = t.find("img")
            caption = t.find("div", class_="caption") or t
            name, permalink, avatar, last_modified = None, None, None, None

            if a and a.get("href"):
                permalink = a["href"] if a["href"].startswith("http") else f"https://researchmap.jp{a['href']}"
            if img:
                avatar = img.get("ng-src") or img.get("src")
            if caption:
                name_node = caption.find(lambda tag: tag.name in ["div", "a"] and tag.get_text(strip=True))
                if name_node:
                    name = name_node.get_text(strip=True)
                p = caption.find("p")
                if p:
                    last_modified = p.get_text(strip=True)

            if name and not is_noise(name):
                results.append({
                    "name": name,
                    "permalink": permalink,
                    "avatar": avatar,
                    "last_modified": last_modified,
                })
        except Exception:
            continue
    return results

def scrape_paper_abstract(paper_url, session=None, render_browser=None):
    soup = render_page_get_soup(paper_url, render_browser) if render_browser else get_soup(paper_url, session=session)
    if not soup:
        return ""
    
    abstract_heading = soup.find(lambda tag: tag.name in ["th", "dt", "h2", "h3", "h4", "div", "span"] and tag.get_text(strip=True) in ["概要", "抄録", "Abstract", "Description"])
    if abstract_heading:
        parent = abstract_heading.parent
        target = parent.find(["td", "dd"]) or abstract_heading.find_next_sibling(["td", "dd", "div", "p"])
        if target:
            return target.get_text(strip=True)

    desc = soup.find(class_=re.compile(r"description|abstract|outline", re.I))
    if desc:
        return desc.get_text(strip=True)

    return ""

def extract_detailed_papers(profile_base, session=None, render_browser=None, max_papers=10, lang="ja", author_name="", fetch_abstract=True, delay=0.8):
    url = f"{profile_base}/published_papers?lang={lang}&limit={max_papers}"
    soup = render_page_get_soup(url, render_browser) if render_browser else get_soup(url, session=session)
    if not soup:
        return []

    papers = []
    list_items = soup.select(".published_papers li, .rm-cv-list li, ul.list-group li")
    if not list_items:
        list_items = soup.find_all("li")

    cleaned_author = re.sub(r"（.*?）|\(.*?\)", "", author_name)
    author_norm = re.sub(r"[\s,]+", "", cleaned_author).lower()

    for item in list_items:
        if len(papers) >= max_papers:
            break

        link_tag = None
        for a in item.find_all("a", href=True):
            href = a["href"]
            if "/published_papers/" in href:
                link_tag = a
                break

        if not link_tag:
            continue

        title = link_tag.get_text(strip=True)
        if not title or is_noise(title):
            continue

        raw_href = link_tag["href"]
        paper_url = raw_href if raw_href.startswith("http") else f"https://researchmap.jp{raw_href}"
        item_text = item.get_text(separator=" | ", strip=True)

        is_first_author = False
        parts = [p.strip() for p in item_text.split("|") if p.strip()]
        if parts:
            first_part_norm = re.sub(r"[\s,]+", "", parts[0]).lower()
            if author_norm and (author_norm in first_part_norm or first_part_norm.startswith(author_norm[:3])):
                is_first_author = True

        abstract = ""
        if fetch_abstract and paper_url:
            time.sleep(delay)
            abstract = scrape_paper_abstract(paper_url, session=session, render_browser=render_browser)

        papers.append({
            "title": title,
            "paper_url": paper_url,
            "meta_text": item_text,
            "is_first_author": is_first_author,
            "abstract": abstract
        })

    return papers

def scrape_profile(profile_base, session=None, render_browser=None, lang="ja", max_papers=10, author_name="", fetch_abstract=True):
    target_url = f"{profile_base}?lang={lang}"
    soup = render_page_get_soup(target_url, render_browser) if render_browser else get_soup(target_url, session=session)
    if not soup:
        return None

    data = {
        "名前": author_name,
        "researchmap_url": target_url,
        "research_keywords": [],
        "research_areas": [],
        "papers": [],
        "coauthors": [],
        "education": [],
        "career": [],
        "association_memberships": [],
    }

    if lang == "ja":
        kw_rk = ["研究キーワード", "キーワード"]
        kw_ra = ["研究分野", "研究領域"]
        kw_ed = ["学歴", "学位"]
        kw_cr = ["経歴", "職歴"]
        kw_am = ["所属学会", "学会"]
    else:
        kw_rk = ["Research Keywords", "Keywords"]
        kw_ra = ["Research Areas", "Research Interests"]
        kw_ed = ["Education", "Degree"]
        kw_cr = ["Research Experience", "Career"]
        kw_am = ["Association Memberships", "Memberships"]

    data["research_keywords"] = extract_section_by_heading(soup, kw_rk)
    data["research_areas"] = extract_section_by_heading(soup, kw_ra)
    data["coauthors"] = extract_coauthors_from_soup(soup)
    data["education"] = extract_section_by_heading(soup, kw_ed)
    data["career"] = extract_section_by_heading(soup, kw_cr)
    data["association_memberships"] = extract_section_by_heading(soup, kw_am)

    data["papers"] = extract_detailed_papers(
        profile_base=profile_base,
        session=session,
        render_browser=render_browser,
        max_papers=max_papers,
        lang=lang,
        author_name=author_name,
        fetch_abstract=fetch_abstract
    )

    URLS = {
        "education": f"{profile_base}/education?lang={lang}&limit=100",
        "research_interests": f"{profile_base}/research_interests?lang={lang}",
        "research_areas": f"{profile_base}/research_areas?lang={lang}",
        "association_memberships": f"{profile_base}/association_memberships?lang={lang}&limit=100",
    }

    try:
        edu = extract_items_from_url(URLS["education"], session=session, render_browser=render_browser)
        if edu:
            data["education"] = edu
        ri = extract_items_from_url(URLS["research_interests"], session=session, render_browser=render_browser)
        if ri:
            data["research_keywords"] = ri
        ra = extract_items_from_url(URLS["research_areas"], session=session, render_browser=render_browser)
        if ra:
            data["research_areas"] = ra
        am = extract_items_from_url(URLS["association_memberships"], session=session, render_browser=render_browser)
        if am:
            data["association_memberships"] = am
    except Exception as e:
        print(f"Warning on fetching subpages for {target_url}: {e}")

    return data

def process_all(json_path_ja, json_path_en, out_ja="remp_yugo_faculty_ja.json", out_en="remp_yugo_faculty_en.json", limit=None, delay=1.5, max_papers=10, render=False, fetch_abstract=True):
    session = requests.Session()
    playwright_controller = None
    browser = None
    
    if render:
        try:
            from playwright.sync_api import sync_playwright
            playwright_controller = sync_playwright().start()
            browser = playwright_controller.chromium.launch(headless=True)
        except Exception as e:
            print(f"Failed to start Playwright: {e}")
            raise

    json_path_ja = resolve_path(json_path_ja)
    json_path_en = resolve_path(json_path_en)
    out_ja = resolve_path(out_ja)
    out_en = resolve_path(out_en)

    with open(json_path_ja, "r", encoding="utf-8") as f:
        rows_ja = json.load(f)
    with open(json_path_en, "r", encoding="utf-8") as f:
        rows_en = json.load(f)

    total = min(len(rows_ja), len(rows_en))
    print(f"Loaded {total} researchers from JSON.")

    results_ja = []
    results_en = []

    count = 0
    for row_ja, row_en in zip(rows_ja, rows_en):
        if limit and count >= limit:
            break
        
        name_ja = row_ja.get("名前", "").strip()
        name_en = row_en.get("名前", "").strip()

        if not name_ja:
            continue

        clean_search_name = re.sub(r"（.*?）|\(.*?\)", "", name_ja)
        clean_search_name = re.sub(r"\s+", "", clean_search_name)

        print(f"\n[{count+1}/{total}] Searching: {name_ja} ({name_en})")
        q = quote_plus(clean_search_name)
        search_url = f"https://researchmap.jp/researchers?q={q}"
        soup = render_page_get_soup(search_url, browser) if browser else get_soup(search_url, session=session)
        profile_url = find_profile_link_from_search(soup, name_ja)

        if not profile_url:
            print(f"  Profile not found for {name_ja}")
            results_ja.append({"名前": name_ja, "found": False})
            results_en.append({"名前": name_en, "found": False})
        else:
            profile_base = profile_url.split('?')[0].rstrip('/')
            print(f"  Found: {profile_base}")
            
            print("    Scraping Japanese (JA)...")
            data_ja = scrape_profile(
                profile_base, session=session, render_browser=browser,
                lang="ja", max_papers=max_papers, author_name=name_ja, fetch_abstract=fetch_abstract
            ) or {"名前": name_ja, "found": False}
            results_ja.append(data_ja)
            
            print("    Scraping English (EN)...")
            data_en = scrape_profile(
                profile_base, session=session, render_browser=browser,
                lang="en", max_papers=max_papers, author_name=name_en, fetch_abstract=fetch_abstract
            ) or {"名前": name_en, "found": False}
            results_en.append(data_en)

        count += 1
        time.sleep(delay)

    # 日本語版と英語版を個別のJSONファイルとして保存
    with open(out_ja, "w", encoding="utf-8") as f_ja:
        json.dump(results_ja, f_ja, ensure_ascii=False, indent=2)
    print(f"\nSaved Japanese researchmap data to: {out_ja}")

    with open(out_en, "w", encoding="utf-8") as f_en:
        json.dump(results_en, f_en, ensure_ascii=False, indent=2)
    print(f"Saved English researchmap data to: {out_en}")
    
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
    parser = argparse.ArgumentParser(description="Scrape researchmap data exclusively and save to independent JSON files.")
    parser.add_argument("--json-ja", default="ridb_yugo_faculty_ja.json", help="Input Japanese RIDB JSON")
    parser.add_argument("--json-en", default="ridb_yugo_faculty_en.json", help="Input English RIDB JSON")
    parser.add_argument("--out-ja", default="remp_yugo_faculty_ja.json", help="Output Japanese researchmap JSON")
    parser.add_argument("--out-en", default="remp_yugo_faculty_en.json", help="Output English researchmap JSON")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of researchers (0 = all)")
    parser.add_argument("--delay", type=float, default=1.5, help="Delay between requests in seconds")
    parser.add_argument("--max-papers", type=int, default=10, help="Maximum number of papers to inspect per researcher")
    parser.add_argument("--no-abstract", action="store_true", help="Skip fetching individual paper abstract")
    parser.add_argument("--render", action="store_true", help="Use Playwright for dynamic rendering")
    args = parser.parse_args()

    lim = args.limit if args.limit and args.limit > 0 else None
    process_all(
        json_path_ja=args.json_ja,
        json_path_en=args.json_en,
        out_ja=args.out_ja,
        out_en=args.out_en,
        limit=lim,
        delay=args.delay,
        max_papers=args.max_papers,
        render=args.render,
        fetch_abstract=not args.no_abstract
    )