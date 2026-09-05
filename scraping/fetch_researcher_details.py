import json
import time
import re
import argparse
import os
from pathlib import Path

import requests
from bs4 import BeautifulSoup

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
RESEARCHMAP_COOKIE_FILE = SCRIPT_DIR / "researchmap_session_cookie.txt"
RESEARCHMAP_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Code/1.131.0 Chrome/148.0.7778.280 Electron/42.7.0 Safari/537.36"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

NOISE_TOKENS = {
    "日本語 | English", "新規登録", "ログイン", "ホーム", "マイポータルへ", "マイポータル",
    "研究ブログ", "資料公開", "English", "20件", "50件", "100件", "Login", "Register", "Home"
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

def is_noise(text: str) -> bool:
    t = text.strip()
    if not t or t in NOISE_TOKENS or t in COMMON_HEADER_LABELS or len(t) <= 1:
        return True
    return False


def resolve_path(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    candidate = PROJECT_ROOT / path
    if candidate.exists() or not path.exists():
        return candidate
    return path

def get_soup(url, session=None, timeout=15):
    s = session or requests
    try:
        resp = s.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print(f"Failed to GET {url}: {e}")
        return None

def render_page_get_soup(url, browser, timeout=30):
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

def normalize_name(name_str: str) -> str:
    s = re.sub(r"[（(].*?[）)]", "", str(name_str))
    return re.sub(r"[\s,]+", "", s).lower().strip()


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


def apply_researchmap_session(session):
    session.headers.update({"User-Agent": RESEARCHMAP_USER_AGENT})
    cookie_value = load_researchmap_cookie()
    if cookie_value and "=" in cookie_value:
        name, value = cookie_value.split("=", 1)
        session.cookies.set(name, value, domain="researchmap.jp", path="/")


def apply_researchmap_browser_state(context):
    context.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")
    cookie_value = load_researchmap_cookie()
    if cookie_value and "=" in cookie_value:
        name, value = cookie_value.split("=", 1)
        context.add_cookies([
            {"name": name, "value": value, "domain": "researchmap.jp", "path": "/"}
        ])


def load_json_rows(path_str):
    if not path_str:
        return None
    path = resolve_path(path_str)
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def match_index_url(index_data, target_names):
    normalized_targets = [normalize_name(name) for name in target_names if name]

    for item in index_data:
        idx_norm = normalize_name(item.get("name", ""))
        if not idx_norm:
            continue
        for target_norm in normalized_targets:
            if target_norm == idx_norm or target_norm in idx_norm or idx_norm in target_norm:
                return item.get("profile_url")
    return None


def extract_row_name(row):
    for key in ("名前", "name", "氏名"):
        value = row.get(key)
        if value:
            return str(value).strip()
    return ""


def build_targets_from_rows(index_data, rows_ja, rows_en=None):
    targets = []
    rows_en = rows_en or []

    for i, row_ja in enumerate(rows_ja):
        name_ja = extract_row_name(row_ja)
        if not name_ja:
            continue

        name_en = name_ja
        if i < len(rows_en):
            candidate_en = extract_row_name(rows_en[i])
            if candidate_en:
                name_en = candidate_en

        matched_url = match_index_url(index_data, [name_ja, name_en])
        targets.append({
            "name_ja": name_ja,
            "name_en": name_en,
            "url": matched_url,
        })

    return targets

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

def extract_items_from_url(url, session=None, render_browser=None):
    soup = render_page_get_soup(url, render_browser) if render_browser else get_soup(url, session=session)
    if not soup:
        return []
    items = []
    seen = set()
    for n in soup.find_all("li"):
        text = n.get_text(separator=" ", strip=True)
        if text and text not in seen and not is_noise(text):
            seen.add(text)
            items.append(text)
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
    url = f"{profile_base}/published_papers?lang={lang}"
    soup = render_page_get_soup(url, render_browser) if render_browser else get_soup(url, session=session)
    if not soup:
        return []

    papers = []
    list_items = soup.select(".published_papers li, .rm-cv-list li, ul.list-group li")
    if not list_items:
        list_items = soup.find_all("li")

    cleaned_author = normalize_name(author_name)

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
            first_part_norm = normalize_name(parts[0])
            if cleaned_author and (cleaned_author in first_part_norm or first_part_norm.startswith(cleaned_author[:2])):
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
        "education": f"{profile_base}/education?lang={lang}",
        "research_interests": f"{profile_base}/research_interests?lang={lang}",
        "research_areas": f"{profile_base}/research_areas?lang={lang}",
        "association_memberships": f"{profile_base}/association_memberships?lang={lang}",
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

def run_fetch(index_file, filter_yugo=True, json_ja="ridb_yugo_faculty_ja.json", json_en="ridb_yugo_faculty_en.json",
              out_ja="remp_yugo_faculty_ja.json", out_en="remp_yugo_faculty_en.json",
              start_idx=0, limit=None, delay=1.5, max_papers=10, render=False, fetch_abstract=True,
              single_source=False):

    index_path = resolve_path(index_file)
    with open(index_path, "r", encoding="utf-8") as f:
        index_data = json.load(f)

    session = requests.Session()
    apply_researchmap_session(session)
    playwright_controller = None
    browser = None
    use_browser = render or bool(load_researchmap_cookie())
    if use_browser:
        try:
            from playwright.sync_api import sync_playwright
            playwright_controller = sync_playwright().start()
            browser = playwright_controller.chromium.launch(headless=True).new_context(
                user_agent=RESEARCHMAP_USER_AGENT,
                locale="ja-JP"
            )
            apply_researchmap_browser_state(browser)
        except Exception as e:
            print(f"Failed to start Playwright: {e}")
            raise

    rows_ja = load_json_rows(json_ja)
    if rows_ja is None:
        raise FileNotFoundError(f"RIDB input not found: {json_ja}")

    rows_en = None if single_source else load_json_rows(json_en)
    if not single_source and rows_en is None:
        print(f"English RIDB file not found: {json_en}. Falling back to Japanese names for EN output.")

    targets = build_targets_from_rows(index_data, rows_ja, None if single_source else rows_en)

    # スライス処理
    if limit:
        targets = targets[start_idx:start_idx + limit]
    else:
        targets = targets[start_idx:]

    print(f"Total targets to scrape: {len(targets)}")
    matched_targets = sum(1 for t in targets if t["url"])
    print(f"Matched profile URLs: {matched_targets}/{len(targets)}")
    results_ja = []
    results_en = []

    for i, t in enumerate(targets, 1):
        name_ja = t["name_ja"]
        name_en = t["name_en"]
        p_url = t["url"]

        print(f"\n[{i}/{len(targets)}] Processing: {name_ja}")
        if not p_url:
            print(f"  Profile URL not in index for {name_ja}")
            results_ja.append({"名前": name_ja, "found": False})
            results_en.append({"名前": name_en, "found": False})
            continue

        print(f"  Target URL: {p_url}")
        print("    Scraping Japanese (JA)...")
        data_ja = scrape_profile(p_url, session=session, render_browser=browser, lang="ja", max_papers=max_papers, author_name=name_ja, fetch_abstract=fetch_abstract)
        results_ja.append(data_ja or {"名前": name_ja, "found": False})

        print("    Scraping English (EN)...")
        data_en = scrape_profile(p_url, session=session, render_browser=browser, lang="en", max_papers=max_papers, author_name=name_en, fetch_abstract=fetch_abstract)
        results_en.append(data_en or {"名前": name_en, "found": False})

        time.sleep(delay)

    with open(out_ja, "w", encoding="utf-8") as f:
        json.dump(results_ja, f, ensure_ascii=False, indent=2)
    print(f"\nSaved JA data to {out_ja}")

    with open(out_en, "w", encoding="utf-8") as f:
        json.dump(results_en, f, ensure_ascii=False, indent=2)
    print(f"Saved EN data to {out_en}")

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
    parser = argparse.ArgumentParser(description="Fetch details from researchmap index.")
    parser.add_argument("--index", default="kanazawa_researchers_index.json", help="Index file path")
    parser.add_argument("--all", action="store_true", help="Scrape all index researchers (Default: filter by yugo faculty)")
    parser.add_argument("--json-ja", default="ridb_yugo_faculty_ja.json", help="Input Japanese RIDB JSON")
    parser.add_argument("--json-en", default="ridb_yugo_faculty_en.json", help="Input English RIDB JSON")
    parser.add_argument("--out-ja", default="remp_yugo_faculty_ja.json", help="Output Japanese JSON")
    parser.add_argument("--out-en", default="remp_yugo_faculty_en.json", help="Output English JSON")
    parser.add_argument("--start-idx", type=int, default=0, help="Start target index")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of researchers (0 = all)")
    parser.add_argument("--delay", type=float, default=1.5, help="Delay between requests")
    parser.add_argument("--max-papers", type=int, default=10, help="Papers limit per researcher")
    parser.add_argument("--no-abstract", action="store_true", help="Skip fetching paper abstract")
    parser.add_argument("--single-source", action="store_true", help="Use only the Japanese RIDB file and reuse it for EN output names")
    parser.add_argument("--render", action="store_true", help="Use Playwright for dynamic rendering")
    args = parser.parse_args()

    lim = args.limit if args.limit and args.limit > 0 else None
    run_fetch(
        index_file=args.index,
        filter_yugo=not args.all,
        json_ja=args.json_ja,
        json_en=args.json_en,
        out_ja=args.out_ja,
        out_en=args.out_en,
        start_idx=args.start_idx,
        limit=lim,
        delay=args.delay,
        max_papers=args.max_papers,
        render=args.render,
        fetch_abstract=not args.no_abstract,
        single_source=args.single_source
    )