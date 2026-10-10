"""
01 — Scrape RIDB faculty directories → ridb_yugo_faculty_{ja,en}.json

Usage:
  python scraping/01_ridb_scrape.py
  python scraping/01_ridb_scrape.py --only-new   # scrape configured add-on orgs only
  python scraping/01_ridb_scrape.py --refresh    # re-fetch even if id already stored
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# Windows cp932 コンソールでも落ちないようにする
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# --- 設定 ---
BASE_URL = "https://ridb.kanazawa-u.ac.jp/public/"
REQUEST_TIMEOUT = 20
REQUEST_DELAY = 1.5
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "ridb"

# 既存コア（融合研究域）+ 追加対象の学部・系
CORE_SOURCES = {
    "ja": [
        {
            "label": "融合研究域",
            "url": "https://ridb.kanazawa-u.ac.jp/public/list.php?org1_cd=31",
        },
    ],
    "en": [
        {
            "label": "Institute of Transdisciplinary Sciences",
            "url": "https://ridb.kanazawa-u.ac.jp/public/list_en.php?org1_cd=31",
        },
    ],
}

# 追加収集対象（org2）
NEW_SOURCES = {
    "ja": [
        {
            "label": "人間社会研究域 経済学経営学系",
            "url": "https://ridb.kanazawa-u.ac.jp/public/list.php?org2_cd=320800",
        },
        {
            "label": "人間社会研究域 地域創造学系",
            "url": "https://ridb.kanazawa-u.ac.jp/public/list.php?org2_cd=321400",
        },
        {
            "label": "人間社会研究域 国際学系",
            "url": "https://ridb.kanazawa-u.ac.jp/public/list.php?org2_cd=321600",
        },
        {
            "label": "理工研究域 電子情報通信学系",
            "url": "https://ridb.kanazawa-u.ac.jp/public/list.php?org2_cd=341600",
        },
        {
            "label": "理工研究域 フロンティア工学系",
            "url": "https://ridb.kanazawa-u.ac.jp/public/list.php?org2_cd=341400",
        },
        {
            "label": "医薬保健研究域 保健学系",
            "url": "https://ridb.kanazawa-u.ac.jp/public/list.php?org2_cd=360600",
        },
    ],
    "en": [
        {
            "label": "Faculty of Economics and Management",
            "url": "https://ridb.kanazawa-u.ac.jp/public/list_en.php?org2_cd=320800",
        },
        {
            "label": "Faculty of Regional Development Studies",
            "url": "https://ridb.kanazawa-u.ac.jp/public/list_en.php?org2_cd=321400",
        },
        {
            "label": "Faculty of International Studies",
            "url": "https://ridb.kanazawa-u.ac.jp/public/list_en.php?org2_cd=321600",
        },
        {
            "label": "Faculty of Electrical, Information and Communication Engineering",
            "url": "https://ridb.kanazawa-u.ac.jp/public/list_en.php?org2_cd=341600",
        },
        {
            "label": "Faculty of Frontier Engineering",
            "url": "https://ridb.kanazawa-u.ac.jp/public/list_en.php?org2_cd=341400",
        },
        {
            "label": "Faculty of Health Sciences",
            "url": "https://ridb.kanazawa-u.ac.jp/public/list_en.php?org2_cd=360600",
        },
    ],
}

# 取得したい項目の見出しテキスト（日本語/英語の対応）
TARGET_HEADERS = {
    "ja": {
        "affiliation_title": ["所属組織・役職等"],
        "birth_date": ["生年月"],
        "edu_field": ["教育分野"],
        "lab": ["所属研究室等"],
        "lab_web": ["研究室webサイト"],
        "education": ["学歴"],
        "work_history": ["職歴"],
        "society": ["所属学会"],
        "specialty": ["専門分野"],
        "keywords": ["専門分野キーワード"],
        "research_theme": ["研究課題"],
        "joint_research": ["共同研究希望テーマ"],
        "papers": ["論文", "研究業績"],
        "books": ["著書"],
        "presentations": ["学会発表", "講演・口頭発表等"],
        "other_publications": ["その他（報告書など）", "その他", "その他の業績"],
        "art_fieldwork": ["芸術・フィールドワーク"],
        "patents": ["特許"],
        "kaken": ["科研費", "科学研究費助成事業"],
        "competitive_funding": ["競争的資金・寄付金等", "競争的資金", "共同研究・競争的資金等の研究課題"],
        "awards": ["受賞学術賞", "受賞"],
        "internal_committees": ["学内委員会委員等"],
        "education_records": ["担当経験のある科目", "教育実績"],
        "social_contributions": ["社会貢献", "社会活動"],
    },
    "en": {
        "affiliation_title": ["Affiliation/Title", "Faculty, Affiliation"],
        "birth_date": ["Year & Month of Birth", "Date of Birth"],
        "edu_field": ["Education Field", "College and School Educational Field"],
        "lab": ["Laboratory"],
        "lab_web": ["Lab Website", "Laboratory Website"],
        "education": ["Education", "Academic Background"],
        "work_history": ["Work Experience", "Career"],
        "society": ["Academic Societies", "Academic Society"],
        "specialty": ["Field of Specialization", "Specialities"],
        "keywords": ["Keywords", "Speciality Keywords"],
        "research_theme": ["Research Themes"],
        "joint_research": ["Joint Research Themes", "Theme to the desired joint research"],
        "papers": ["Papers", "Articles"],
        "books": ["Books"],
        "presentations": ["Presentations", "Conference Presentations"],
        "other_publications": ["Other Publications", "Others (Reports, etc.)", "Others"],
        "art_fieldwork": ["Arts and Fieldwork"],
        "patents": ["Patents"],
        "kaken": ["Grants-in-Aid", "KAKENHI"],
        "competitive_funding": [
            "Competitive research funding,Contribution",
            "Competitive Funding",
            "Research Projects",
            "Competitive Research Funding",
        ],
        "awards": ["Award", "Awards"],
        "internal_committees": ["Internal Committees"],
        "education_records": ["Courses Taught", "Educational Activities"],
        "social_contributions": ["Social Contributions", "Social Activities"],
    },
}


def fetch_soup(session, url):
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return BeautifulSoup(response.content, "html.parser")


def normalize_text(value):
    return " ".join(str(value).split()) if value else ""


def get_detail_text(soup, header_texts):
    """詳細ページ内で特定の <h3> を探し、隣の <p> テキストを抽出する"""
    if isinstance(header_texts, str):
        header_texts = [header_texts]

    header = soup.find(
        lambda tag: tag.name == "h3"
        and any(header_text in tag.get_text(strip=True) for header_text in header_texts)
    )
    if not header:
        return ""

    sibling = header.find_next_sibling()
    while sibling and sibling.name != "h3":
        if sibling.name == "p":
            text = normalize_text(sibling.get_text(" ", strip=True))
            if text:
                value = sibling
                break
        nested = sibling.find_all(["h4", "p"])
        if nested:
            text = normalize_text(" ".join(node.get_text(" ", strip=True) for node in nested))
            if text:
                return text
        sibling = sibling.find_next_sibling()
    else:
        value = None

    if not value:
        return ""

    link = value.find("a", href=True)
    if link:
        text = normalize_text(value.get_text(" ", strip=True))
        return f"{text} ({link['href']})" if text else link["href"]

    return normalize_text(value.get_text(" ", strip=True))


def get_detail_items(soup, header_texts):
    """見出し直後の ul または br 区切りの p を、項目ごとの配列として取得する。"""
    if isinstance(header_texts, str):
        header_texts = [header_texts]

    header = soup.find(
        lambda tag: tag.name == "h3"
        and normalize_text(tag.get_text(" ", strip=True)) in header_texts
    )
    if not header:
        return []

    items = []
    sibling = header.find_next_sibling()
    while sibling and sibling.name != "h3":
        if sibling.name == "ul" and "list-disc" in sibling.get("class", []):
            values = [item.get_text(" ", strip=True) for item in sibling.find_all("li", recursive=False)]
        elif sibling.name == "p":
            values = []
            for fragment in sibling.find_all(string=True):
                text = normalize_text(fragment)
                if text:
                    values.append(text)
        else:
            sibling = sibling.find_next_sibling()
            continue

        for value in values:
            text = normalize_text(value)
            if not text or text in items or "全て表示" in text or "javascript:void" in text:
                continue
            items.append(text)
        if items:
            break
        sibling = sibling.find_next_sibling()

    return items


def get_detail_items_for_headers(soup, header_groups):
    """複数の見出しに分かれた同一カテゴリをまとめて取得する。"""
    items = []
    for headers in header_groups:
        for item in get_detail_items(soup, headers):
            if item not in items:
                items.append(item)
    return items


def get_email(soup):
    link = soup.select_one('a[href^="mailto:"]')
    return link.get("href", "")[7:] if link else ""


def get_photo_url(soup, page_url):
    image = soup.select_one('meta[property="og:image"]')
    if image and image.get("content"):
        return urljoin(page_url, image["content"])

    photo_box = soup.select_one("#sub p.pht_box[style]")
    if photo_box:
        match = re.search(
            r"background-image\s*:\s*url\(['\"]?([^)'\"]+)",
            photo_box["style"],
            re.IGNORECASE,
        )
        if match:
            return urljoin(page_url, match.group(1))

    image = soup.select_one("#sub img[src]")
    return urljoin(page_url, image["src"]) if image else ""


def get_personal_url(soup, page_url):
    for link in soup.select("a[href]"):
        href = urljoin(page_url, link["href"])
        if href.startswith("http") and "ridb.kanazawa-u.ac.jp" not in href:
            return href
    return ""


def get_researcher_id(page_url):
    return parse_qs(urlparse(page_url).query).get("id", [""])[0]


def get_lab_website(soup):
    label = soup.find(
        lambda tag: tag.name == "a"
        and tag.find(
            "img",
            alt=lambda alt: alt and ("Website" in alt or "ウェブサイト" in alt or "研究室" in alt),
        )
    )
    if label and label.has_attr("href"):
        return label["href"]

    for link in soup.select("#sub a[href]"):
        href = link.get("href", "")
        if href.startswith("http") and "kanazawa-u.ac.jp" not in href:
            return href
    return ""


def get_max_page_count(soup):
    page_numbers = []
    for link in soup.find_all("a", href=True):
        match = re.search(r"[?&]page=(\d+)", link["href"])
        if match:
            page_numbers.append(int(match.group(1)))
    return max(page_numbers) if page_numbers else 1


def build_list_url(base_url, page_number):
    if page_number <= 1:
        return base_url
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}page={page_number}"


def parse_list_rows(soup, page_url):
    rows = []
    for tr in soup.select("table tr"):
        cells = tr.find_all(["th", "td"])
        if len(cells) < 5:
            continue

        first_cell_text = normalize_text(cells[0].get_text(" ", strip=True))
        if first_cell_text == "No." or not first_cell_text.isdigit():
            continue

        name_cell = cells[1]
        name_link = name_cell.find("a", href=True)
        detail_url = urljoin(page_url, name_link["href"]) if name_link else ""

        rows.append(
            {
                "name": normalize_text(name_cell.get_text(" ", strip=True)),
                "affiliation": normalize_text(cells[2].get_text(" ", strip=True)),
                "position": normalize_text(cells[3].get_text(" ", strip=True)),
                "keywords": normalize_text(cells[4].get_text(" ", strip=True)),
                "detail_url": detail_url,
            }
        )

    return rows


def load_existing_results(output_filename: Path) -> list:
    if not output_filename.exists():
        return []
    try:
        with open(output_filename, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[WARN] 既存ファイルの読み込みに失敗 ({output_filename}): {e}")
        return []


def merge_by_id(existing: list, updates: list) -> list:
    """ridb id をキーに更新・追加（重複防止）。"""
    by_id = {}
    order = []

    for row in existing:
        rid = str((row.get("basic") or {}).get("id") or "").strip()
        key = rid or f"name:{(row.get('basic') or {}).get('name', '')}"
        if key not in by_id:
            order.append(key)
        by_id[key] = row

    for row in updates:
        rid = str((row.get("basic") or {}).get("id") or "").strip()
        key = rid or f"name:{(row.get('basic') or {}).get('name', '')}"
        if key not in by_id:
            order.append(key)
        by_id[key] = row

    return [by_id[k] for k in order]


def scrape_researcher_detail(session, row, headers_dict, lang):
    detail_data = {k: "" for k in headers_dict.keys()}
    detail_soup = None
    photo_url = ""
    email = ""
    personal_url = ""

    if row["detail_url"]:
        try:
            time.sleep(REQUEST_DELAY)
            detail_soup = fetch_soup(session, row["detail_url"])
            lab_website = get_lab_website(detail_soup)
            photo_url = get_photo_url(detail_soup, row["detail_url"])
            email = get_email(detail_soup)
            personal_url = get_personal_url(detail_soup, row["detail_url"])

            for key, header_label in headers_dict.items():
                detail_data[key] = get_detail_text(detail_soup, header_label)

            if lab_website:
                detail_data["lab_web"] = lab_website

        except Exception as e:
            print(f"  [WARN] {row['name']} の詳細ページ取得エラー: {e}")

    return {
        "basic": {
            "id": get_researcher_id(row["detail_url"]),
            "name": row["name"],
            "affiliation": detail_data["affiliation_title"] or row["affiliation"],
            "position": row["position"],
            "birth_date": detail_data["birth_date"],
            "photo_url": photo_url,
            "email": email,
            "lab_website": detail_data["lab_web"],
            "personal_url": personal_url or row["detail_url"],
        },
        "education": {
            "education_field": detail_data["edu_field"],
            "lab": detail_data["lab"],
            "academic_background": detail_data["education"],
            "career": detail_data["work_history"],
        },
        "research": {
            "specialty": detail_data["specialty"] or row["keywords"],
            "keywords": detail_data["keywords"] or row["keywords"],
            "research_themes": detail_data["research_theme"],
            "joint_research": detail_data["joint_research"],
        },
        "outputs": {
            "papers": get_detail_items(detail_soup, headers_dict["papers"]) if detail_soup else [],
            "books": get_detail_items(detail_soup, headers_dict["books"]) if detail_soup else [],
            "presentations": get_detail_items(detail_soup, headers_dict["presentations"])
            if detail_soup
            else [],
            "other_publications": get_detail_items(detail_soup, headers_dict["other_publications"])
            if detail_soup
            else [],
            "art_fieldwork": get_detail_items(detail_soup, headers_dict["art_fieldwork"])
            if detail_soup
            else [],
            "patents": get_detail_items(detail_soup, headers_dict["patents"]) if detail_soup else [],
        },
        "funding": {
            "kaken": get_detail_items(detail_soup, headers_dict["kaken"]) if detail_soup else [],
            "competitive_funding": get_detail_items(
                detail_soup, headers_dict["competitive_funding"]
            )
            if detail_soup
            else [],
        },
        "awards": get_detail_items(detail_soup, headers_dict["awards"]) if detail_soup else [],
        "internal_committees": get_detail_items(detail_soup, headers_dict["internal_committees"])
        if detail_soup
        else [],
        "society": [detail_data["society"]] if detail_data["society"] else [],
        "education_records": get_detail_items_for_headers(
            detail_soup,
            [
                headers_dict["education_records"],
                ["大学院担当授業科目", "Graduate School Courses"],
                ["学士課程担当授業科目", "Undergraduate Courses"],
            ],
        )
        if detail_soup
        else [],
        "social_contributions": get_detail_items_for_headers(
            detail_soup,
            [
                headers_dict["social_contributions"],
                ["国際事業協力", "International Cooperation"],
                ["留学生参加の社会活動", "Social Activities Involving International Students"],
                ["審議会等の参加", "Advisory Councils"],
                ["講演可能なテーマ", "Topics Available for Lectures"],
                ["その他公的社会活動", "Other Public Social Activities"],
            ],
        )
        if detail_soup
        else [],
    }


def collect_list_rows(session, sources):
    """複数一覧URLから研究者行を収集し、detail_url / id で重複除去。"""
    seen_ids = set()
    seen_urls = set()
    all_rows = []

    for source in sources:
        label = source["label"]
        url = source["url"]
        print(f"  一覧: {label}")
        print(f"    URL: {url}")
        try:
            first_soup = fetch_soup(session, url)
        except Exception as e:
            print(f"    [WARN] 一覧取得エラー: {e}")
            continue

        max_page_count = get_max_page_count(first_soup)
        print(f"    {max_page_count} ページ")

        for page_number in range(1, max_page_count + 1):
            page_url = build_list_url(url, page_number)
            if page_number > 1:
                time.sleep(REQUEST_DELAY)
                try:
                    soup = fetch_soup(session, page_url)
                except Exception as e:
                    print(f"    [WARN] ページ {page_number} 取得エラー: {e}")
                    continue
            else:
                soup = first_soup

            rows = parse_list_rows(soup, page_url)
            added = 0
            for row in rows:
                rid = get_researcher_id(row["detail_url"]) if row["detail_url"] else ""
                if rid and rid in seen_ids:
                    continue
                if row["detail_url"] and row["detail_url"] in seen_urls:
                    continue
                if rid:
                    seen_ids.add(rid)
                if row["detail_url"]:
                    seen_urls.add(row["detail_url"])
                row["_source_label"] = label
                all_rows.append(row)
                added += 1
            print(f"    page {page_number}/{max_page_count}: +{added} (raw {len(rows)})")

    return all_rows


def scrape_ridb_list(lang="ja", sources=None, merge=True, skip_existing=True):
    headers_dict = TARGET_HEADERS[lang]
    output_filename = OUTPUT_DIR / f"ridb_yugo_faculty_{lang}.json"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if sources is None:
        sources = CORE_SOURCES[lang] + NEW_SOURCES[lang]

    existing = load_existing_results(output_filename) if merge else []
    existing_ids = {
        str((row.get("basic") or {}).get("id") or "").strip()
        for row in existing
        if str((row.get("basic") or {}).get("id") or "").strip()
    }
    print(
        f"[{lang.upper()}] 既存レコード: {len(existing)} "
        f"(skip_existing={skip_existing}, merge={merge})"
    )

    try:
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0"})

        print(f"[{lang.upper()}] {len(sources)} 件の一覧ソースを走査します...")
        researcher_rows = collect_list_rows(session, sources)
        print(f"[{lang.upper()}] 一覧ユニーク件数: {len(researcher_rows)}")

        to_fetch = []
        skipped = 0
        for row in researcher_rows:
            rid = get_researcher_id(row["detail_url"]) if row["detail_url"] else ""
            if skip_existing and rid and rid in existing_ids:
                skipped += 1
                continue
            to_fetch.append(row)

        print(f"[{lang.upper()}] 詳細取得対象: {len(to_fetch)} / スキップ: {skipped}")

        updates = []
        for i, row in enumerate(to_fetch, 1):
            print(f"  [{i}/{len(to_fetch)}] {row['name']} ({row.get('_source_label', '')})")
            researcher_info = scrape_researcher_detail(session, row, headers_dict, lang)
            updates.append(researcher_info)
            print(
                f"    [OK] 取得完了: {researcher_info['basic']['name']} "
                f"id={researcher_info['basic']['id']}"
            )

            # 途中保存（長時間実行の安全策）
            if merge and i % 10 == 0:
                merged_partial = merge_by_id(existing, updates)
                with open(output_filename, "w", encoding="utf-8") as f:
                    json.dump(merged_partial, f, ensure_ascii=False, indent=4)
                print(f"    ... checkpoint saved ({len(merged_partial)} total)")

        if merge:
            results = merge_by_id(existing, updates)
        else:
            results = updates

        with open(output_filename, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=4)

        print(
            f"\n[{lang.upper()}] 完了: 新規/更新 {len(updates)} 件 → "
            f"合計 {len(results)} 件 → {output_filename}\n"
        )
        return {"lang": lang, "fetched": len(updates), "skipped": skipped, "total": len(results)}

    except Exception as e:
        print(f"[{lang.upper()}] エラーが発生しました: {e}")
        return {"lang": lang, "error": str(e)}


def parse_args():
    parser = argparse.ArgumentParser(description="01: scrape RIDB faculty lists into JSON.")
    parser.add_argument(
        "--only-new",
        action="store_true",
        help="Scrape only NEW_SOURCES (additional org2 departments), not the core org1 list.",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-fetch researchers even if their RIDB id already exists in the output JSON.",
    )
    parser.add_argument(
        "--no-merge",
        action="store_true",
        help="Overwrite output with this run only (do not merge into existing JSON).",
    )
    parser.add_argument(
        "--lang",
        choices=["ja", "en", "both"],
        default="both",
        help="Language to scrape (default: both).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    langs = ["ja", "en"] if args.lang == "both" else [args.lang]
    merge = not args.no_merge
    skip_existing = not args.refresh

    summaries = []
    for lang in langs:
        sources = NEW_SOURCES[lang] if args.only_new else (CORE_SOURCES[lang] + NEW_SOURCES[lang])
        summaries.append(
            scrape_ridb_list(
                lang=lang,
                sources=sources,
                merge=merge,
                skip_existing=skip_existing,
            )
        )

    print("=== RIDB scrape summary ===")
    for s in summaries:
        print(s)
