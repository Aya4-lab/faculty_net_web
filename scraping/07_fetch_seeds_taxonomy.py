"""
07 — Scrape RIDB 研究分野別シーズ集 taxonomy → data/raw/ridb/seeds_taxonomy.json

Hierarchy:
  大分野 (major)  e.g. 情報学, 工学
    └─ 研究分野 (field)  e.g. 知能情報学, 認知科学  (cat code from kekka.php)

Usage:
  python scraping/07_fetch_seeds_taxonomy.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "data" / "raw" / "ridb" / "seeds_taxonomy.json"
SEEDS_URL = "https://ridb.kanazawa-u.ac.jp/seeds/"
REQUEST_TIMEOUT = 30

COUNT_RE = re.compile(r"^(.+?)\s*\((\d+)\)\s*$")
CAT_RE = re.compile(r"cat=(\d+)")

# RIDB HTML nests the last 情報学 field under 環境学; fix by cat code.
CAT_MAJOR_OVERRIDE: dict[int, str] = {
    1305: "情報学",  # エンタテインメント・ゲーム情報学
}


def scrape_taxonomy() -> dict:
    response = requests.get(SEEDS_URL, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    response.encoding = response.apparent_encoding
    soup = BeautifulSoup(response.text, "html.parser")

    majors: list[dict] = []
    fields_flat: list[dict] = []

    for h3 in soup.find_all("h3"):
        major_name = h3.get_text(" ", strip=True)
        if major_name in {"キーワード検索について"}:
            continue

        ul = h3.find_next_sibling("ul")
        if ul is None:
            continue

        fields: list[dict] = []
        for li in ul.find_all("li", recursive=False):
            text = li.get_text(" ", strip=True)
            match = COUNT_RE.match(text)
            if not match:
                continue
            field_name = match.group(1).strip()
            count = int(match.group(2))
            anchor = li.find("a")
            href = (anchor.get("href") if anchor else "") or ""
            cat_match = CAT_RE.search(href)
            cat = int(cat_match.group(1)) if cat_match else None
            resolved_major = CAT_MAJOR_OVERRIDE.get(cat, major_name) if cat else major_name
            url = urljoin(SEEDS_URL, href) if href else ""
            field = {
                "cat": cat,
                "name": field_name,
                "major": resolved_major,
                "seeds_count": count,
                "url": url,
            }
            fields.append(field)
            fields_flat.append(field)

        if fields:
            majors.append({"name": major_name, "fields": fields})

    # Rebuild majors after overrides so 1305 sits under 情報学.
    by_major: dict[str, list[dict]] = {}
    order: list[str] = []
    for field in fields_flat:
        major = field["major"]
        if major not in by_major:
            by_major[major] = []
            order.append(major)
        by_major[major].append(
            {
                "cat": field["cat"],
                "name": field["name"],
                "seeds_count": field["seeds_count"],
                "url": field["url"],
            }
        )

    return {
        "source": SEEDS_URL,
        "majors": [{"name": name, "fields": by_major[name]} for name in order],
    }


def main() -> None:
    payload = scrape_taxonomy()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    n_major = len(payload["majors"])
    n_field = sum(len(m["fields"]) for m in payload["majors"])
    print("=== 07_fetch_seeds_taxonomy ===")
    print(f"output : {OUT_PATH}")
    print(f"majors : {n_major}")
    print(f"fields : {n_field}")
    for major in payload["majors"]:
        print(f"  {major['name']}: {len(major['fields'])}")


if __name__ == "__main__":
    main()
