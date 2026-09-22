"""Step 1: faculty profile + lightweight first-author paper URL meta (no paper detail pages)."""
import json
import time
import re
import argparse
import os
from pathlib import Path
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
RESEARCHMAP_DATA_DIR = PROJECT_ROOT / "data" / "raw" / "researchmap"
RESEARCHMAP_COOKIE_FILE = RESEARCHMAP_DATA_DIR / "researchmap_session_cookie.txt"
LEGACY_COOKIE_FILE = SCRIPT_DIR / "researchmap_session_cookie.txt"
RESEARCHMAP_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Code/1.131.0 Chrome/148.0.7778.280 Electron/42.7.0 Safari/537.36"

CITATION_RE = re.compile(r"(?:被引用件数|Citations?|Cited by)[:：\s]*(\d+)", re.I)
REQUEST_DELAY = 1.5

NOISE_TOKENS = {
    # JA / EN navigation & buttons
    "日本語 | English", "新規登録", "ログイン", "ホーム", "マイポータルへ", "マイポータル",
    "研究ブログ", "資料公開", "English", "Sign up", "Login", "Register", "Home", "My Portal",
    # legacy count labels (also covered by COUNT_ITEMS_RE)
    "20件", "50件", "100件", "20 items", "50 items", "100 items",
}

COMMON_HEADER_LABELS = {
    # JA section headers / menu
    "研究分野", "経歴", "学歴", "論文", "研究キーワード", "所属学会", "所属学協会",
    "受賞", "担当経験のある科目(授業)", "担当経験のある科目", "共同研究・競争的資金等の研究課題",
    "委員歴", "MISC", "書籍等出版物", "講演・口頭発表等", "Works(作品等)", "産業財産権", "ホーム",
    "社会貢献活動", "メディア報道", "その他",
    # EN section headers / menu
    "Research Areas", "Research Experience", "Research History", "Education", "Published Papers",
    "Papers", "Research Keywords", "Association Memberships", "Professional Memberships",
    "Awards", "Works", "Presentations", "Misc", "Misc.", "Media Coverage",
    "Social Contributions", "Others", "Books and Other Publications", "Teaching Experience",
    "Research Projects", "Industrial Property Rights",
    # ★ 今回混入したサブページ見出しを追加
    "Research Interests", "Committee Memberships",
}

COUNT_ITEMS_RE = re.compile(r"^\d+\s*(?:items|件)$", re.I)
KANAZAWA_AFFIL_MARKERS = ("金沢大学", "Kanazawa University")


def is_noise(text: str) -> bool:
    t = text.strip()
    if not t or len(t) <= 1:
        return True
    if t in NOISE_TOKENS or t in COMMON_HEADER_LABELS:
        return True
    if COUNT_ITEMS_RE.match(t):
        return True
    return False


def empty_faculty_record(name: str, researchmap_url: str = "", found: bool = False):
    return {
        "名前": name,
        "researchmap_url": researchmap_url,
        "found": found,
        "avatar_url": "",
        "research_keywords": [],
        "research_areas": [],
        "career": [],
        "education": [],
        "association_memberships": [],
        "teaching_experience": [],
        "misc": [],
        "books_etc": [],
        "presentations": [],
        "research_projects": [],
        "coauthors": [],
        "papers": [],
    }


def merge_unique_items(existing, new_items):
    out = list(existing or [])
    seen = set(out)
    for item in new_items or []:
        if item and item not in seen and not is_noise(item):
            seen.add(item)
            out.append(item)
    return out


def normalize_image_url(src: str) -> str:
    """Keep only normal http(s) or /img/ paths; drop data:image Base64 embeds."""
    if not src:
        return ""
    s = str(src).strip()
    if s.startswith("data:image/"):
        return ""
    if s.startswith("https://") or s.startswith("http://"):
        return s
    if s.startswith("/img/") or s.startswith("/"):
        return f"https://researchmap.jp{s}" if s.startswith("/") else s
    return ""


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


def render_page_get_soup(url, browser, timeout=30, delay=REQUEST_DELAY):
    """Fetch a page exclusively via Playwright (requests are blocked with 403)."""
    if browser is None:
        print(f"Playwright browser is required to fetch {url}")
        return None
    try:
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=timeout * 1000)
        page.wait_for_timeout(600)
        content = page.content()
        page.close()
        if delay and delay > 0:
            time.sleep(delay)
        return BeautifulSoup(content, "html.parser")
    except Exception as e:
        print(f"Failed to render {url} with Playwright: {e}")
        if delay and delay > 0:
            time.sleep(delay)
        return None


def strip_furigana(name: str) -> str:
    """Remove parenthetical reading (ふりがな) and return kanji/base name only."""
    return re.sub(r"[（(].*?[）)]", "", str(name or "")).strip()


def normalize_name(name_str: str) -> str:
    s = strip_furigana(name_str)
    return re.sub(r"[\s,]+", "", s).lower().strip()


def names_match(candidate_name: str, target_name: str) -> bool:
    c = normalize_name(candidate_name)
    t = normalize_name(target_name)
    if not c or not t:
        return False
    return c == t or t in c or c in t


def affiliation_is_kanazawa(affiliation: str) -> bool:
    if not affiliation:
        return False
    return any(marker in affiliation for marker in KANAZAWA_AFFIL_MARKERS)


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
    basic = row.get("basic")
    if isinstance(basic, dict):
        for key in ("名前", "name", "氏名"):
            value = basic.get(key)
            if value:
                return str(value).strip()
    return ""


def absolute_researchmap_url(href: str) -> str:
    href = (href or "").strip()
    if not href:
        return ""
    if href.startswith("http"):
        return href.split("?")[0].split("#")[0].rstrip("/")
    if href.startswith("/"):
        return f"https://researchmap.jp{href.split('?')[0].split('#')[0].rstrip('/')}"
    return ""


def extract_search_candidates(soup):
    """Parse researchmap researcher search results into name/affiliation/url triples."""
    if not soup:
        return []

    candidates = []
    seen = set()
    cards = soup.select(
        "ul.search-results > li, .rm-search-result-item, .list-group-item, li.media, div.media, .search-result"
    )
    if not cards:
        cards = soup.find_all(["li", "article", "div"], recursive=True)

    exclude_fragments = (
        "/search", "/static", "/assets", "/help", "/terms", "/privacy",
        "/regist", "/researchers", "/achievements", "/new_accounts", "/auth",
        "/login", "/logout", "/languages", "javascript:",
    )

    for card in cards:
        for a in card.find_all("a", href=True):
            href = a["href"].strip()
            if any(k in href for k in exclude_fragments) or href in ("/", "#"):
                continue

            name = a.get_text(separator=" ", strip=True)
            if is_noise(name) or re.match(r"^\d+$", name):
                continue

            profile_url = absolute_researchmap_url(href)
            if not profile_url or profile_url in seen:
                continue

            # Skip non-profile site paths; do not rely on trailing ID shape (Hiro01 etc.)
            path = profile_url.replace("https://researchmap.jp", "").replace("http://researchmap.jp", "")
            if path.count("/") != 1 or path in ("", "/"):
                continue

            parent = a.find_parent(["li", "div", "article"]) or card
            affiliation = ""
            if parent:
                for line in parent.get_text(separator="\n", strip=True).splitlines():
                    line_str = line.strip()
                    if line_str and line_str != name and (
                        "大学" in line_str or "University" in line_str or "College" in line_str
                    ):
                        affiliation = line_str
                        break

            seen.add(profile_url)
            candidates.append({
                "name": name,
                "affiliation": affiliation,
                "profile_url": profile_url,
            })

    return candidates


def search_kanazawa_profile_url(name, render_browser=None):
    """Fallback: search researchmap and pick matching profile."""
    query = strip_furigana(name)
    if not query:
        return None

    search_url = f"https://researchmap.jp/researchers?q={quote_plus(query)}"
    print(f"  Fallback search: {search_url}")
    soup = render_page_get_soup(search_url, render_browser)
    if not soup:
        return None

    candidates = extract_search_candidates(soup)
    # 対象の氏名と完全一致する候補を抽出
    name_matched_cands = [c for c in candidates if names_match(c["name"], query)]

    if not name_matched_cands:
        print(f"  Fallback: no name match for {query}")
        return None

    # ① 同姓同名が1人しかいない場合：所属判定をスキップして確定
    if len(name_matched_cands) == 1:
        target = name_matched_cands[0]
        print(f"  Fallback matched (unique candidate): {target['name']} -> {target['profile_url']}")
        return target["profile_url"]

    # ② 同姓同名が複数いる場合：所属テキストに「金沢大学」が含まれるものを優先
    for cand in name_matched_cands:
        if affiliation_is_kanazawa(cand.get("affiliation", "")):
            print(f"  Fallback matched (Kanazawa affiliation): {cand['name']} / {cand['affiliation']} -> {cand['profile_url']}")
            return cand["profile_url"]

    # ③ それでも見つからない場合（プロフィールページ本文で確認する最終手段）
    for cand in name_matched_cands:
        p_soup = render_page_get_soup(cand["profile_url"], render_browser)
        if p_soup and ("金沢大学" in p_soup.get_text() or "Kanazawa University" in p_soup.get_text()):
            print(f"  Fallback matched (profile page check): {cand['name']} -> {cand['profile_url']}")
            return cand["profile_url"]

    print(f"  Fallback: multiple candidates exist, but none matched Kanazawa for {query}")
    return None


def resolve_profile_url(index_data, name_ja, name_en, render_browser=None):
    matched = match_index_url(index_data, [name_ja, name_en])
    if matched:
        return matched

    for query_name in (name_ja, name_en):
        if not query_name:
            continue
        found = search_kanazawa_profile_url(query_name, render_browser=render_browser)
        if found:
            return found
    return None


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
    for tag in soup.find_all(re.compile(r"^h[1-6]$")):
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


def extract_items_from_url(url, render_browser=None):
    soup = render_page_get_soup(url, render_browser)
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


def extract_avatar_url(soup):
    """Faculty profile main avatar URL (skip Base64 data:image embeds)."""
    if not soup:
        return ""

    selectors = [
        "div.rm-cv-avatar img",
        ".rm-cv-avatar img",
        ".profile-avatar img",
        "img[ng-src*='avatar']",
        "img[src*='avatar']",
        ".rm-cv-header img",
        "#rm-cv-avatar img",
    ]
    for sel in selectors:
        img = soup.select_one(sel)
        if not img:
            continue
        src = img.get("ng-src") or img.get("src") or ""
        url = normalize_image_url(src)
        if url:
            return url

    # Fallback: first non-noise profile image near header
    for img in soup.find_all("img", limit=20):
        src = img.get("ng-src") or img.get("src") or ""
        if "avatar" in src.lower() or "photo" in src.lower() or "/img/" in src:
            url = normalize_image_url(src)
            if url:
                return url
    return ""


def extract_coauthors_from_soup(soup):
    """Coauthors with name + permalink only (no avatars), deduplicated."""
    results = []
    if not soup:
        return results
    outer = soup.find(id="rm-coauthors-outer") or soup.find(class_="rm-side-block-avatar-list")
    if not outer:
        return results

    seen = set()
    thumbs = outer.find_all("div", class_=lambda c: c and ("thumbnail" in c.split() or "col-sm" in c))
    for t in thumbs:
        try:
            a = t.find("a", href=True)
            caption = t.find("div", class_="caption") or t
            name, permalink = None, None

            if a and a.get("href"):
                permalink = a["href"] if a["href"].startswith("http") else f"https://researchmap.jp{a['href']}"
            if caption:
                name_node = caption.find(lambda tag: tag.name in ["div", "a"] and tag.get_text(strip=True))
                if name_node:
                    name = name_node.get_text(strip=True)

            if not name or is_noise(name):
                continue

            dedupe_key = (permalink or "").rstrip("/") or normalize_name(name)
            if not dedupe_key or dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            results.append({
                "name": name,
                "permalink": permalink or "",
            })
        except Exception:
            continue
    return results


def extract_author_leading_text(item, title_link):
    """Text appearing before the paper title link (= author area)."""
    chunks = []
    for child in item.children:
        if child is title_link:
            break
        if getattr(child, "name", None) == "a" and child.get("href") and "/published_papers/" in child["href"]:
            break
        if hasattr(child, "get_text"):
            chunks.append(child.get_text(" ", strip=True))
        elif isinstance(child, str):
            chunks.append(child.strip())
    return " ".join(c for c in chunks if c).strip()


def has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff\uf900-\ufaff]", text or ""))


def normalize_for_match(text: str) -> str:
    s = strip_furigana(text or "").lower().strip()
    s = s.replace("．", ".").replace("。", ".").replace("·", " ").replace("・", " ")
    s = re.sub(r"[,，、/;]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def compact_for_match(text: str) -> str:
    return re.sub(r"[\s.]+", "", normalize_for_match(text))


def _add_romaji_variants(targets: set, family: str, given: str):
    family = normalize_for_match(family)
    given = normalize_for_match(given)
    if not family:
        return
    targets.add(family)
    targets.add(compact_for_match(family))
    if not given:
        return
    gi = given[0] if given else ""
    variants = [
        f"{given} {family}",
        f"{family} {given}",
        f"{gi}. {family}",
        f"{family}, {gi}.",
        f"{family}, {gi}",
        f"{gi} {family}",
        f"{family} {gi}",
        f"{gi}.{family}",
        f"{family},{gi}.",
        f"{family} {gi}.",
    ]
    for v in variants:
        targets.add(normalize_for_match(v))
        targets.add(compact_for_match(v))


def build_name_match_targets(*names) -> list:
    """
    Build lowercase / punctuation-light name variants for flexible author matching.
    Includes kanji full/family, romaji both orders, and initial forms.
    """
    targets = set()
    for raw in names:
        if not raw:
            continue
        base = strip_furigana(str(raw)).strip()
        if not base:
            continue

        norm = normalize_for_match(base)
        compact = compact_for_match(base)
        if norm:
            targets.add(norm)
        if compact:
            targets.add(compact)

        if has_cjk(base):
            tokens = [t for t in re.split(r"\s+", base) if t]
            if tokens:
                family = tokens[0]
                targets.add(normalize_for_match(family))
                targets.add(compact_for_match(family))
            if len(tokens) >= 2:
                full = "".join(tokens)
                targets.add(normalize_for_match(full))
                targets.add(compact_for_match(full))
                targets.add(normalize_for_match(" ".join(tokens)))
        else:
            if "," in base:
                family, given = [x.strip() for x in base.split(",", 1)]
                _add_romaji_variants(targets, family, given)
            else:
                tokens = [t for t in norm.split() if t]
                if len(tokens) >= 2:
                    # Prefer Family Given (common for JP romaji) and also reverse
                    family, given = tokens[0], " ".join(tokens[1:])
                    _add_romaji_variants(targets, family, given)
                    _add_romaji_variants(targets, tokens[-1], " ".join(tokens[:-1]))
                elif len(tokens) == 1:
                    targets.add(tokens[0])
                    targets.add(compact_for_match(tokens[0]))

    # Drop ultra-short tokens that cause false positives (keep CJK surnames of len>=1 is ok;
    # for latin require length >= 2)
    cleaned = set()
    for t in targets:
        if not t:
            continue
        if has_cjk(t):
            cleaned.add(t)
        elif len(compact_for_match(t)) >= 2:
            cleaned.add(t)
    return sorted(cleaned, key=len, reverse=True)


def is_author_match(candidate_str: str, targets) -> bool:
    """True if candidate author text contains any faculty name variant."""
    if not candidate_str or not targets:
        return False
    cand_norm = normalize_for_match(candidate_str)
    cand_compact = compact_for_match(candidate_str)
    if not cand_norm and not cand_compact:
        return False
    for t in targets:
        t_norm = normalize_for_match(t)
        t_compact = compact_for_match(t)
        if t_compact and t_compact in cand_compact:
            return True
        if t_norm and t_norm in cand_norm:
            return True
    return False


def split_author_list(author_segment: str) -> list:
    if not author_segment:
        return []
    parts = re.split(r"\s*[,、;/]\s*|\s+and\s+", author_segment, flags=re.I)
    return [p.strip() for p in parts if p.strip()]


def resolve_author_segment(item, title_link, item_text, title, targets):
    """Pick the author-list segment from list-item text (researchmap: title | authors | ...)."""
    leading = ""
    if item is not None:
        leading = extract_author_leading_text(item, title_link)
    parts = [p.strip() for p in (item_text or "").split("|") if p.strip()]

    candidates = []
    if leading:
        candidates.append(leading)
    for p in parts:
        if title and (p == title or (len(title) >= 12 and p.startswith(title[:12]))):
            continue
        # Skip citation / referee labels
        if parse_citations(p) and len(p) < 40:
            continue
        if p in ("査読有り", "筆頭著者", "最終著者", "責任著者", "Lead author", "Corresponding author"):
            continue
        candidates.append(p)

    for c in candidates:
        if is_author_match(c, targets):
            return c
    if len(parts) >= 2:
        return parts[1]
    return leading or ""


def classify_author_role(author_segment: str, targets):
    """
    Return 'first' if faculty matches the first author token,
    'co' if matched in 2nd+ position, else None.
    """
    if not author_segment or not targets:
        return None

    authors = split_author_list(author_segment)
    if authors:
        if is_author_match(authors[0], targets):
            return "first"
        for author in authors[1:]:
            if is_author_match(author, targets):
                return "co"
        # Whole-segment match but not on token 0 -> treat as co-author
        if is_author_match(author_segment, targets):
            return "co"
        return None

    if is_author_match(author_segment, targets):
        return "first"
    return None


def parse_citations(text: str) -> int:
    m = CITATION_RE.search(text or "")
    if not m:
        return 0
    try:
        return int(m.group(1))
    except ValueError:
        return 0


def extract_paper_url_meta(profile_base, render_browser=None, max_papers=5, lang="ja",
                           author_name="", author_names=None):
    """
    Lightweight paper list scrape only (no per-paper detail pages).
    Prefer first-author papers by citations; fill remainder with co-author papers up to max_papers.
    """
    url = f"{profile_base}/published_papers?lang={lang}"
    soup = render_page_get_soup(url, render_browser)
    if not soup:
        return []

    names = [n for n in (author_names or [author_name]) if n]
    if author_name and author_name not in names:
        names.insert(0, author_name)
    targets = build_name_match_targets(*names)

    first_author_papers = []
    co_author_papers = []
    seen_urls = set()

    list_items = soup.select(".published_papers li, .rm-cv-list li, ul.list-group li")
    if not list_items:
        list_items = soup.find_all("li")

    for item in list_items:
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
        if paper_url in seen_urls:
            continue
        item_text = item.get_text(separator=" | ", strip=True)

        author_segment = resolve_author_segment(item, link_tag, item_text, title, targets)
        role = classify_author_role(author_segment, targets)
        if role is None:
            continue

        citations = parse_citations(item_text)
        entry = {
            "title": title,
            "paper_url": paper_url,
            "meta_text": item_text,
            "is_first_author": role == "first",
            "citations": citations,
        }
        seen_urls.add(paper_url)
        if role == "first":
            first_author_papers.append(entry)
        else:
            co_author_papers.append(entry)

    first_author_papers.sort(key=lambda p: p.get("citations", 0), reverse=True)
    co_author_papers.sort(key=lambda p: p.get("citations", 0), reverse=True)

    selected = first_author_papers[:max_papers]
    if len(selected) < max_papers:
        need = max_papers - len(selected)
        selected.extend(co_author_papers[:need])

    return selected


def scrape_profile(profile_base, render_browser=None, lang="ja", max_papers=5,
                   author_name="", author_names=None):
    profile_base = (profile_base or "").rstrip("/")
    target_url = f"{profile_base}?lang={lang}"
    soup = render_page_get_soup(target_url, render_browser)
    if not soup:
        return None

    data = empty_faculty_record(author_name, researchmap_url=target_url, found=True)
    data["avatar_url"] = extract_avatar_url(soup)

    if lang == "ja":
        heading_map = {
            "research_keywords": ["研究キーワード", "キーワード"],
            "research_areas": ["研究分野", "研究領域"],
            "education": ["学歴", "学位"],
            "career": ["経歴", "職歴"],
            "association_memberships": ["所属学会", "所属学協会", "学会"],
            "teaching_experience": ["担当経験のある科目(授業)", "担当経験のある科目"],
            "misc": ["MISC", "その他"],
            "books_etc": ["書籍等出版物", "書籍"],
            "presentations": ["講演・口頭発表等", "講演・口頭発表"],
            "research_projects": ["共同研究・競争的資金等の研究課題", "競争的資金", "科研費"],
        }
    else:
        heading_map = {
            "research_keywords": ["Research Keywords", "Keywords"],
            "research_areas": ["Research Areas", "Research Interests"],
            "education": ["Education", "Degree"],
            "career": ["Research Experience", "Research History", "Career"],
            "association_memberships": ["Association Memberships", "Professional Memberships", "Memberships"],
            "teaching_experience": ["Teaching Experience"],
            "misc": ["Misc.", "Misc", "Others"],
            "books_etc": ["Books and Other Publications", "Books"],
            "presentations": ["Presentations"],
            "research_projects": ["Research Projects"],
        }

    for key, keywords in heading_map.items():
        data[key] = extract_section_by_heading(soup, keywords)

    data["coauthors"] = extract_coauthors_from_soup(soup)

    names_for_papers = list(author_names or [])
    if author_name and author_name not in names_for_papers:
        names_for_papers.insert(0, author_name)

    data["papers"] = extract_paper_url_meta(
        profile_base=profile_base,
        render_browser=render_browser,
        max_papers=max_papers,
        lang=lang,
        author_name=author_name,
        author_names=names_for_papers,
    )

    URLS = {
        "research_keywords": f"{profile_base}/research_interests?lang={lang}",
        "research_areas": f"{profile_base}/research_areas?lang={lang}",
        "education": f"{profile_base}/education?lang={lang}&limit=100",
        "career": f"{profile_base}/research_experience?lang={lang}&limit=100",
        "association_memberships": f"{profile_base}/association_memberships?lang={lang}&limit=100",
        "teaching_experience": f"{profile_base}/teaching_experience?lang={lang}&limit=100",
        "misc": f"{profile_base}/misc?lang={lang}&limit=100",
        "books_etc": f"{profile_base}/books_etc?lang={lang}&limit=100",
        "presentations": f"{profile_base}/presentations?lang={lang}&limit=100",
        "research_projects": f"{profile_base}/research_projects?lang={lang}&limit=100",
    }

    try:
        for key, sub_url in URLS.items():
            items = extract_items_from_url(sub_url, render_browser=render_browser)
            if items:
                data[key] = merge_unique_items(data.get(key), items)
    except Exception as e:
        print(f"Warning on fetching subpages for {target_url}: {e}")

    return data


def run_fetch(index_file, json_ja="data/raw/ridb/ridb_yugo_faculty_ja.json", json_en="data/raw/ridb/ridb_yugo_faculty_en.json",
              out_ja=None, out_en=None,
              start_idx=0, limit=None, delay=REQUEST_DELAY, max_papers=5,
              single_source=False):

    index_path = resolve_path(index_file)
    with open(index_path, "r", encoding="utf-8") as f:
        index_data = json.load(f)

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
    except Exception as e:
        print(f"Failed to start Playwright: {e}")
        raise

    rows_ja = load_json_rows(json_ja)
    if rows_ja is None:
        raise FileNotFoundError(f"RIDB input not found: {json_ja}")

    rows_en = None if single_source else load_json_rows(json_en)
    if not single_source and rows_en is None:
        print(f"English RIDB file not found: {json_en}. Falling back to Japanese names for EN output.")

    if out_ja is None:
        out_ja = RESEARCHMAP_DATA_DIR / "remp_yugo_faculty_ja.json"
    if out_en is None:
        out_en = RESEARCHMAP_DATA_DIR / "remp_yugo_faculty_en.json"

    out_ja = resolve_path(str(out_ja))
    out_en = resolve_path(str(out_en))

    targets = build_targets_from_rows(index_data, rows_ja, None if single_source else rows_en)

    if limit:
        targets = targets[start_idx:start_idx + limit]
    else:
        targets = targets[start_idx:]

    print(f"Total targets to scrape: {len(targets)}")
    matched_targets = sum(1 for t in targets if t["url"])
    print(f"Matched profile URLs in index: {matched_targets}/{len(targets)}")
    results_ja = []
    results_en = []

    try:
        for i, t in enumerate(targets, 1):
            name_ja = t["name_ja"]
            name_en = t["name_en"]
            p_url = t["url"]

            print(f"\n[{i}/{len(targets)}] Processing: {name_ja}")
            if not p_url:
                p_url = resolve_profile_url(
                    index_data, name_ja, name_en, render_browser=browser
                )
                t["url"] = p_url

            if not p_url:
                print(f"  Profile URL not found for {name_ja}")
                results_ja.append(empty_faculty_record(name_ja, found=False))
                results_en.append(empty_faculty_record(name_en, found=False))
                continue

            print(f"  Target URL: {p_url}")
            name_variants = [name_ja, name_en]
            print("    Scraping Japanese (JA)...")
            data_ja = scrape_profile(
                p_url, render_browser=browser, lang="ja",
                max_papers=max_papers, author_name=name_ja, author_names=name_variants,
            )
            results_ja.append(data_ja or empty_faculty_record(name_ja, found=False))

            print("    Scraping English (EN)...")
            data_en = scrape_profile(
                p_url, render_browser=browser, lang="en",
                max_papers=max_papers, author_name=name_en, author_names=name_variants,
            )
            results_en.append(data_en or empty_faculty_record(name_en, found=False))

            if delay and delay > 0:
                time.sleep(delay)
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

    out_ja.parent.mkdir(parents=True, exist_ok=True)
    out_en.parent.mkdir(parents=True, exist_ok=True)

    with open(out_ja, "w", encoding="utf-8") as f:
        json.dump(results_ja, f, ensure_ascii=False, indent=2)
    print(f"\nSaved JA data to {out_ja}")

    with open(out_en, "w", encoding="utf-8") as f:
        json.dump(results_en, f, ensure_ascii=False, indent=2)
    print(f"Saved EN data to {out_en}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Step 1: fetch faculty profiles and top first-author paper URL meta from researchmap."
    )
    parser.add_argument("--index", default=str(RESEARCHMAP_DATA_DIR / "kanazawa_researchers_index.json"), help="Index file path")
    parser.add_argument("--json-ja", default="data/raw/ridb/ridb_yugo_faculty_ja.json", help="Input Japanese RIDB JSON")
    parser.add_argument("--json-en", default="data/raw/ridb/ridb_yugo_faculty_en.json", help="Input English RIDB JSON")
    parser.add_argument("--out-ja", default=str(RESEARCHMAP_DATA_DIR / "remp_yugo_faculty_ja.json"), help="Output Japanese JSON")
    parser.add_argument("--out-en", default=str(RESEARCHMAP_DATA_DIR / "remp_yugo_faculty_en.json"), help="Output English JSON")
    parser.add_argument("--start-idx", type=int, default=0, help="Start target index")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of researchers (0 = all)")
    parser.add_argument("--delay", type=float, default=REQUEST_DELAY, help="Extra delay between researchers (per-request delay is also applied)")
    parser.add_argument("--max-papers", type=int, default=5, help="Max first-author papers per researcher (by citations)")
    parser.add_argument("--single-source", action="store_true", help="Use only the Japanese RIDB file and reuse it for EN output names")
    parser.add_argument("--render", action="store_true", help="Deprecated: Playwright is always used")
    args = parser.parse_args()

    lim = args.limit if args.limit and args.limit > 0 else None
    run_fetch(
        index_file=args.index,
        json_ja=args.json_ja,
        json_en=args.json_en,
        out_ja=args.out_ja,
        out_en=args.out_en,
        start_idx=args.start_idx,
        limit=lim,
        delay=args.delay,
        max_papers=args.max_papers,
        single_source=args.single_source,
    )
