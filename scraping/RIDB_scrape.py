import requests
from bs4 import BeautifulSoup
import json
import re
import time
from urllib.parse import urljoin

# --- 設定 ---
BASE_URL = "https://ridb.kanazawa-u.ac.jp/public/"
URLS = {
    "ja": "https://ridb.kanazawa-u.ac.jp/public/list.php?org1_cd=31",
    "en": "https://ridb.kanazawa-u.ac.jp/public/list_en.php?org1_cd=31"
}
REQUEST_TIMEOUT = 20
REQUEST_DELAY = 1

# 取得したい項目の見出しテキスト（日本語/英語の対応）
TARGET_HEADERS = {
    "ja": {
        "affiliation_title": ["所属組織・役職等"],
        "edu_field": ["教育分野"],
        "lab": ["所属研究室等"],
        "lab_web": ["研究室webサイト"],
        "education": ["学歴"],
        "work_history": ["職歴"],
        "society": ["所属学会"],
        "specialty": ["専門分野"],
        "keywords": ["専門分野キーワード"],
        "research_theme": ["研究課題"],
        "joint_research": ["共同研究希望テーマ"]
    },
    "en": {
        "affiliation_title": ["Affiliation/Title", "Faculty, Affiliation"],
        "edu_field": ["Education Field", "College and School Educational Field"],
        "lab": ["Laboratory"],
        "lab_web": ["Lab Website", "Laboratory Website"],
        "education": ["Education", "Academic Background"],
        "work_history": ["Work Experience", "Career"],
        "society": ["Academic Societies", "Academic Society"],
        "specialty": ["Field of Specialization", "Specialities"],
        "keywords": ["Keywords", "Speciality Keywords"],
        "research_theme": ["Research Themes"],
        "joint_research": ["Joint Research Themes", "Theme to the desired joint research"]
    }
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
        lambda tag: tag.name == "h3" and any(header_text in tag.get_text(strip=True) for header_text in header_texts)
    )
    if not header:
        return ""

    value = header.find_next_sibling("p")
    if not value:
        return ""

    link = value.find("a", href=True)
    if link:
        text = normalize_text(value.get_text(" ", strip=True))
        return f"{text} ({link['href']})" if text else link["href"]

    return normalize_text(value.get_text(" ", strip=True))


def get_lab_website(soup):
    label = soup.find(lambda tag: tag.name == "a" and tag.find("img", alt=lambda alt: alt and "Website" in alt))
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

def scrape_ridb_list(lang="ja"):
    url = URLS[lang]
    headers_dict = TARGET_HEADERS[lang]
    output_filename = f'ridb_yugo_faculty_{lang}.json'
    
    # 取得した全研究者のデータを格納するリスト
    results = []

    try:
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0"})

        print(f"[{lang.upper()}] リストページにアクセス中: {url}")
        first_soup = fetch_soup(session, url)
        max_page_count = get_max_page_count(first_soup)
        print(f"{max_page_count} ページ分の一覧を取得します...")

        for page_number in range(1, max_page_count + 1):
            page_url = build_list_url(url, page_number)
            print(f"[{lang.upper()}] 一覧ページ {page_number}/{max_page_count}: {page_url}")
            soup = first_soup if page_number == 1 else fetch_soup(session, page_url)

            researcher_rows = parse_list_rows(soup, page_url)
            print(f"  {len(researcher_rows)} 名の情報を取得します...")

            for row in researcher_rows:
                detail_data = {k: "" for k in headers_dict.keys()}

                if row["detail_url"]:
                    try:
                        time.sleep(REQUEST_DELAY)
                        detail_soup = fetch_soup(session, row["detail_url"])
                        lab_website = get_lab_website(detail_soup)

                        for key, header_label in headers_dict.items():
                            detail_data[key] = get_detail_text(detail_soup, header_label)

                        if lab_website:
                            detail_data["lab_web"] = lab_website

                    except Exception as e:
                        print(f"  ⚠ {row['name']} の詳細ページ取得エラー: {e}")

                researcher_info = {
                    "名前": row["name"],
                    "一覧": {
                        "所属組織": row["affiliation"],
                        "職位": row["position"],
                        "専門分野・キーワード": row["keywords"],
                        "個人URL": row["detail_url"]
                    },
                    "個人URL": {
                        "所属組織・役職等": detail_data["affiliation_title"],
                        "教育分野": detail_data["edu_field"],
                        "所属研究室等": detail_data["lab"],
                        "研究室webサイト": detail_data["lab_web"],
                        "学歴": detail_data["education"],
                        "職歴": detail_data["work_history"],
                        "所属学会": detail_data["society"],
                        "専門分野": detail_data["specialty"],
                        "専門分野キーワード": detail_data["keywords"],
                        "研究課題": detail_data["research_theme"],
                        "共同研究希望テーマ": detail_data["joint_research"]
                    }
                }

                results.append(researcher_info)
                print(f"    ✓ 取得完了: {row['name']}")

        # JSONファイルへの書き出し (日本語等の文字化けを防ぐ設定)
        with open(output_filename, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=4)
            
        print(f"\n[{lang.upper()}] スクリプト完了。{output_filename} に保存しました。\n")

    except Exception as e:
        print(f"[{lang.upper()}] エラーが発生しました: {e}")

if __name__ == '__main__':
    # 日本語版の実行
    scrape_ridb_list("ja")
    # 英語版の実行
    scrape_ridb_list("en")