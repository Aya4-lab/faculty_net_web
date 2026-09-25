"""
08 — Scrape RIDB seeds field pages for researcher membership.

Writes:
  data/raw/ridb/seeds_membership.json
    { "cat": {"field_name", "major", "professor_ridb_ids": [...], ...}, ... }

By default only scrapes fields whose names appear in our faculty specialty
strings (fast). Use --all to scrape every field with seeds_count > 0.

Usage:
  python scraping/08_fetch_seeds_membership.py
  python scraping/08_fetch_seeds_membership.py --all
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
RAW_RIDB = ROOT / "data" / "raw" / "ridb"
TAXONOMY_PATH = RAW_RIDB / "seeds_taxonomy.json"
FACULTY_PATH = RAW_RIDB / "ridb_yugo_faculty_ja.json"
OUT_PATH = RAW_RIDB / "seeds_membership.json"

SEEDS_URL = "https://ridb.kanazawa-u.ac.jp/seeds/"
REQUEST_TIMEOUT = 30
REQUEST_DELAY = 0.6

DETAIL_ID_RE = re.compile(r"detail\.php\?id=(\d+)")
SPECIALTY_SPLIT_RE = re.compile(r"[、,，;/／|]+")


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def split_specialty(raw: str) -> list[str]:
    if not raw:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for part in SPECIALTY_SPLIT_RE.split(raw):
        token = part.strip()
        if token and token not in seen:
            seen.add(token)
            out.append(token)
    return out


def specialty_tokens_in_faculty(faculty: list[dict]) -> set[str]:
    tokens: set[str] = set()
    for row in faculty:
        specialty = (row.get("research") or {}).get("specialty") or ""
        tokens.update(split_specialty(specialty))
    return tokens


def iter_fields(taxonomy: dict) -> list[dict]:
    fields: list[dict] = []
    for major in taxonomy.get("majors") or []:
        major_name = major["name"]
        for field in major.get("fields") or []:
            fields.append(
                {
                    "cat": field.get("cat"),
                    "name": field["name"],
                    "major": major_name,
                    "seeds_count": field.get("seeds_count", 0),
                    "url": field.get("url")
                    or (
                        urljoin(SEEDS_URL, f"kekka.php?cat={field['cat']}")
                        if field.get("cat")
                        else ""
                    ),
                }
            )
    return fields


def scrape_field_members(session: requests.Session, url: str) -> list[str]:
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    response.encoding = response.apparent_encoding
    soup = BeautifulSoup(response.content, "html.parser")
    ids: list[str] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        match = DETAIL_ID_RE.search(anchor["href"])
        if not match:
            continue
        ridb_id = match.group(1)
        if ridb_id in seen:
            continue
        seen.add(ridb_id)
        ids.append(ridb_id)
    return ids


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape RIDB seeds membership")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Scrape all fields with seeds_count > 0 (slow)",
    )
    args = parser.parse_args()

    if not TAXONOMY_PATH.exists():
        raise SystemExit(
            f"Missing {TAXONOMY_PATH}. Run scraping/07_fetch_seeds_taxonomy.py first."
        )

    taxonomy = load_json(TAXONOMY_PATH)
    faculty = load_json(FACULTY_PATH)
    our_ids = {
        str((row.get("basic") or {}).get("id") or "").strip()
        for row in faculty
        if (row.get("basic") or {}).get("id")
    }
    specialty_tokens = specialty_tokens_in_faculty(faculty)
    fields = iter_fields(taxonomy)

    if args.all:
        targets = [f for f in fields if f.get("cat") and f.get("seeds_count", 0) > 0]
    else:
        targets = [
            f
            for f in fields
            if f.get("cat") and f["name"] in specialty_tokens and f.get("seeds_count", 0) > 0
        ]

    print(f"fields to scrape: {len(targets)} (of {len(fields)})")
    session = requests.Session()
    membership: dict[str, dict] = {}

    for i, field in enumerate(targets, start=1):
        cat = str(field["cat"])
        url = field["url"]
        print(f"[{i}/{len(targets)}] cat={cat} {field['name']} ...", flush=True)
        try:
            ridb_ids = scrape_field_members(session, url)
        except Exception as exc:  # noqa: BLE001 — keep scrape running
            print(f"  ERROR: {exc}")
            ridb_ids = []
        our_hits = [rid for rid in ridb_ids if rid in our_ids]
        membership[cat] = {
            "cat": field["cat"],
            "name": field["name"],
            "major": field["major"],
            "url": url,
            "professor_ridb_ids": ridb_ids,
            "yugo_professor_ridb_ids": our_hits,
        }
        print(f"  total={len(ridb_ids)} yugo={len(our_hits)}")
        time.sleep(REQUEST_DELAY)

    payload = {
        "source": SEEDS_URL,
        "mode": "all" if args.all else "specialty_overlap",
        "fields": membership,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("=== 08_fetch_seeds_membership ===")
    print(f"output: {OUT_PATH}")
    print(f"fields: {len(membership)}")


if __name__ == "__main__":
    main()
