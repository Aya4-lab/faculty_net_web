"""Shared syllabus URL / title helpers for the analysis → display pipeline.

Not a numbered pipeline step — imported by 01 / 03 / 04 / 05 scripts.
"""

from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
SYLLABUS_LIST = RAW / "syllabus" / "kanazawa_syllabus.csv"
SYLLABUS_BASIC = RAW / "syllabus" / "all_basic_info.csv"

SYLLABUS_YEAR = 2026
# Fallback only when raw scrape URL is missing (incomplete — prefer 日URL).
SYLLABUS_DETAIL_TMPL = (
    "https://eduweb.sta.kanazawa-u.ac.jp/Portal/Public/Syllabus/DetailMain.aspx"
    f"?lct_year={SYLLABUS_YEAR}&lct_cd={{lct_cd}}&je_cd=1&lct_idx={{lct_idx}}"
)


def _is_usable_detail_url(url: str) -> bool:
    url = (url or "").strip()
    if not url or url.upper() == "N/A":
        return False
    return "DetailMain.aspx" in url and "lct_idx=" in url


def load_syllabus_detail_urls() -> dict[str, str]:
    """timetable code -> full Japanese DetailMain URL (includes lct_idx)."""
    urls: dict[str, str] = {}
    if not SYLLABUS_LIST.exists():
        return urls
    with SYLLABUS_LIST.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            code = (row.get("時間割コード") or "").strip()
            url = (row.get("日URL") or "").strip()
            if not code or code in urls:
                continue
            if _is_usable_detail_url(url):
                urls[code] = url
    return urls


def load_syllabus_titles() -> dict[str, str]:
    """timetable code -> 科目名 (prefer list CSV, fall back to basic info)."""
    titles: dict[str, str] = {}
    if SYLLABUS_LIST.exists():
        with SYLLABUS_LIST.open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                code = (row.get("時間割コード") or "").strip()
                title = (row.get("科目名") or "").strip()
                if code and title and code not in titles:
                    titles[code] = title
    if SYLLABUS_BASIC.exists():
        with SYLLABUS_BASIC.open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                code = (row.get("時間割番号") or "").strip()
                title = (row.get("科目名") or "").strip()
                if code and title and code not in titles:
                    titles[code] = title
    return titles


def syllabus_url_for_code(
    code: str,
    url_by_code: dict[str, str] | None = None,
) -> str:
    """Resolve a syllabus detail URL, preferring scraped 日URL with lct_idx."""
    code = (code or "").strip()
    if not code:
        return ""
    by_code = url_by_code if url_by_code is not None else load_syllabus_detail_urls()
    url = by_code.get(code) or ""
    if _is_usable_detail_url(url):
        return url
    return ""


def join_urls(urls: list[str]) -> str:
    """Stable unique URL list joined by | for CSV storage."""
    out: list[str] = []
    seen: set[str] = set()
    for u in urls:
        u = (u or "").strip()
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(u)
    return "|".join(out)


def split_urls(raw: str | list | None) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(u).strip() for u in raw if str(u).strip()]
    text = str(raw).strip()
    if not text:
        return []
    if "|" in text:
        return [p.strip() for p in text.split("|") if p.strip()]
    return [text]
