"""
01 — Build analysis_data.json (整形・分離)

Raw → Analysis: research/syllabus texts for embedding.
Merges identical courses (same title + overview) and aggregates instructor_ids.

Input (data/raw/):
  researchmap/kanazawa_researchers_index.json  (reference; not embedded)
  researchmap/remp_yugo_faculty_ja.json
  researchmap/remp_yugo_paper_details_ja.json
  ridb/ridb_yugo_faculty_ja.json
  syllabus/all_basic_info.csv
  syllabus/all_attribute.csv

Output:
  data/analysis/analysis_data.json

Flat node lists (professors / courses / papers) — no nesting.
UI metadata (name, photo, affiliation, URLs) is excluded.

Usage:
  python pipeline/01_build_analysis_data.py
"""

from __future__ import annotations

import csv
import html
import json
import re
from pathlib import Path
from typing import Any

from syllabus_meta import (
    load_syllabus_detail_urls,
    syllabus_url_for_code,
)

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT_PATH = ROOT / "data" / "analysis" / "analysis_data.json"

REMP_FACULTY = RAW / "researchmap" / "remp_yugo_faculty_ja.json"
REMP_PAPERS = RAW / "researchmap" / "remp_yugo_paper_details_ja.json"
RESEARCHERS_INDEX = RAW / "researchmap" / "kanazawa_researchers_index.json"
RIDB_FACULTY = RAW / "ridb" / "ridb_yugo_faculty_ja.json"
SYLLABUS_BASIC = RAW / "syllabus" / "all_basic_info.csv"
SYLLABUS_ATTR = RAW / "syllabus" / "all_attribute.csv"

# Syllabus "担当教員" blocks: "山田　太郎 | [ | YAMADA, Taro | ], | 鈴木　花子 | [ | ..."
INSTRUCTOR_BLOCK_RE = re.compile(
    r"([^|]+?)\s*\|\s*\[\s*\|\s*([^|\]]*?)\s*\|\s*\]",
    re.UNICODE,
)
HTML_TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"[ \t\u3000]+")
NEWLINE_RE = re.compile(r"\n{3,}")
READING_RE = re.compile(r"[（(][^）)]*[）)]")


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    text = html.unescape(value)
    text = HTML_TAG_RE.sub(" ", text)
    # Syllabus CSV often joins paragraphs with " | "
    text = re.sub(r"\s*\|\s*", "\n", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = WHITESPACE_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = NEWLINE_RE.sub("\n\n", text)
    return text.strip()


def base_name(name: str) -> str:
    """'相澤 大輝 （あいざわ ひろき）' -> '相澤 大輝'"""
    name = READING_RE.sub("", name or "")
    name = name.replace("\u3000", " ").replace("　", " ")
    return WHITESPACE_RE.sub(" ", name).strip()


def norm_name(name: str) -> str:
    return re.sub(r"\s+", "", base_name(name))


def normalize_for_merge(value: str) -> str:
    """Collapse whitespace / newlines for course identity comparison."""
    return re.sub(r"\s+", "", clean_text(value or ""))


def course_merge_key(title: str, text: str) -> tuple[str, str]:
    return (normalize_for_merge(title), normalize_for_merge(text))


def split_keywords(raw: Any) -> list[str]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        parts: list[str] = []
        for item in raw:
            parts.extend(split_keywords(item))
        return parts
    text = clean_text(str(raw))
    if not text:
        return []
    # researchmap areas look like "分野 / 細目 /"
    text = text.strip(" /｜|")
    chunks = re.split(r"[、,，;/／\n|]+", text)
    out: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        kw = chunk.strip().strip("/").strip()
        if not kw:
            continue
        key = kw.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(kw)
    return out


def merge_keywords(*groups: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for kw in group:
            key = kw.casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append(kw)
    return out


def join_text_parts(parts: list[str]) -> str:
    cleaned = [p for p in (clean_text(p) for p in parts) if p]
    return "\n\n".join(cleaned)


def parse_syllabus_instructors(field: str) -> list[str]:
    """Return Japanese instructor names from the 担当教員 cell."""
    field = field or ""
    names: list[str] = []
    for m in INSTRUCTOR_BLOCK_RE.finditer(field):
        ja = clean_text(m.group(1)).strip(" ,，、")
        if not ja:
            continue
        # Rare: multiple JP names inside one block
        for piece in re.split(r"[、,/／]", ja):
            n = base_name(piece)
            if n and not re.fullmatch(r"[A-Za-z .,'\-]+", n):
                names.append(n)
    if names:
        return names
    # Fallback when English bracket pattern is missing
    head = field.split("|")[0]
    for piece in re.split(r"[、,/／]", head):
        n = base_name(piece)
        if n and not re.fullmatch(r"[A-Za-z .,'\-]+", n):
            names.append(n)
    return names


def build_professors(
    remp: list[dict],
    ridb: list[dict],
) -> tuple[list[dict], dict[str, str], dict[str, str]]:
    """
    Returns:
      professors list,
      norm_name -> professor_id,
      paper_url -> professor_id
    """
    ridb_by_norm = {norm_name(r["basic"]["name"]): r for r in ridb}
    remp_by_norm = {norm_name(r["名前"]): r for r in remp}

    all_norms = sorted(set(ridb_by_norm) | set(remp_by_norm))
    professors: list[dict] = []
    name_to_id: dict[str, str] = {}
    paper_url_to_author: dict[str, str] = {}

    for i, nkey in enumerate(all_norms, start=1):
        r = ridb_by_norm.get(nkey)
        m = remp_by_norm.get(nkey)

        ridb_id = (r or {}).get("basic", {}).get("id")
        prof_id = f"prof_{ridb_id}" if ridb_id else f"prof_{i:04d}"
        name_to_id[nkey] = prof_id

        research = (r or {}).get("research") or {}
        # text: only raw prose fields. Never synthesize sentences from keywords.
        text_parts: list[str] = [
            research.get("research_themes") or "",
            research.get("specialty") or "",
            research.get("joint_research") or "",
        ]
        if m:
            projects = m.get("research_projects") or []
            if isinstance(projects, list):
                text_parts.extend(str(p) for p in projects if p)
            elif projects:
                text_parts.append(str(projects))

        text = join_text_parts(text_parts)
        # Missing prose stays "". Keywords stay a separate list (no backfill into text).
        kw = merge_keywords(
            split_keywords(research.get("keywords")),
            split_keywords((m or {}).get("research_keywords")),
            split_keywords((m or {}).get("research_areas")),
        )

        professors.append(
            {
                "id": prof_id,
                "text": text,
                "keywords": kw,
            }
        )

        if m:
            for paper in m.get("papers") or []:
                url = paper.get("paper_url")
                if url and url not in paper_url_to_author:
                    paper_url_to_author[url] = prof_id

    professors.sort(key=lambda x: x["id"])
    return professors, name_to_id, paper_url_to_author


def load_attribute_keywords(path: Path) -> dict[str, list[str]]:
    """
    Pull content-ish descriptors from all_attribute.csv
    (主に「多文化共修科目の内容」の選択項目).
    """
    by_code: dict[str, list[str]] = {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = (row.get("時間割番号") or "").strip()
            attr = (row.get("属性") or "").strip()
            choice = (row.get("選択項目") or "").strip()
            if not code or not choice:
                continue
            if attr != "多文化共修科目の内容":
                continue
            if choice in {"該当する", "該当しない", "いずれも該当しない"}:
                continue
            by_code[code] = merge_keywords(
                by_code.get(code, []),
                split_keywords(choice),
            )
    return by_code


def build_courses(
    name_to_id: dict[str, str],
    attr_keywords: dict[str, list[str]],
) -> list[dict]:
    """
    Build course nodes, merging rows that share the same subject title and
    normalized syllabus text (主題・学習目標・概要) even when instructors or
    timetable codes differ.

    One node per content group; all matched faculty get taught_by edges via
    instructor_ids. syllabus_urls keeps every distinct detail URL.
    """
    url_by_code = load_syllabus_detail_urls()
    # merge_key -> accumulator
    groups: dict[tuple[str, str], dict] = {}

    with SYLLABUS_BASIC.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            instructors = parse_syllabus_instructors(row.get("担当教員") or "")
            matched_ids: list[str] = []
            for name in instructors:
                pid = name_to_id.get(norm_name(name))
                if pid and pid not in matched_ids:
                    matched_ids.append(pid)
            if not matched_ids:
                continue

            code = (row.get("時間割番号") or "").strip()
            if not code:
                continue

            title = (row.get("科目名") or "").strip()
            text = join_text_parts(
                [
                    row.get("授業の主題") or "",
                    row.get("学習目標") or "",
                    row.get("授業概要") or "",
                ]
            )
            keywords = merge_keywords(
                split_keywords(row.get("キーワード")),
                attr_keywords.get(code, []),
            )
            syllabus_url = syllabus_url_for_code(code, url_by_code)
            key = course_merge_key(title, text)

            if key not in groups:
                groups[key] = {
                    "codes": [],
                    "instructor_ids": [],
                    "syllabus_urls": [],
                    "text": text,
                    "title": title,
                    "keywords": [],
                }
            g = groups[key]
            if code not in g["codes"]:
                g["codes"].append(code)
            for pid in matched_ids:
                if pid not in g["instructor_ids"]:
                    g["instructor_ids"].append(pid)
            if syllabus_url and syllabus_url not in g["syllabus_urls"]:
                g["syllabus_urls"].append(syllabus_url)
            g["keywords"] = merge_keywords(g["keywords"], keywords)
            # Prefer the longest non-empty text as canonical description
            if len(text) > len(g["text"]):
                g["text"] = text
            if title and not g["title"]:
                g["title"] = title

    courses: list[dict] = []
    seen_ids: set[str] = set()
    for g in groups.values():
        codes = sorted(g["codes"])
        instructor_ids = g["instructor_ids"]
        if not codes or not instructor_ids:
            continue
        primary_code = codes[0]
        syllabus_urls = g["syllabus_urls"]
        course_id = f"course_{primary_code}"
        # Rare: same timetable code can appear with different syllabus text;
        # keep IDs unique so downstream UMAP/edges stay 1:1.
        if course_id in seen_ids:
            suffix = 2
            while f"{course_id}__m{suffix}" in seen_ids:
                suffix += 1
            course_id = f"{course_id}__m{suffix}"
        seen_ids.add(course_id)
        courses.append(
            {
                "id": course_id,
                "instructor_id": instructor_ids[0],
                "instructor_ids": instructor_ids,
                "timetable_codes": codes,
                "syllabus_urls": syllabus_urls,
                "syllabus_url": syllabus_urls[0] if syllabus_urls else "",
                "text": g["text"],
                "keywords": g["keywords"],
            }
        )

    courses.sort(key=lambda x: x["id"])
    return courses


def build_papers(
    paper_details: dict[str, dict],
    paper_url_to_author: dict[str, str],
) -> list[dict]:
    papers: list[dict] = []
    for url, detail in paper_details.items():
        author_id = paper_url_to_author.get(url)
        if not author_id:
            # Fallback: infer from researchmap path segment
            # e.g. https://researchmap.jp/Hiro0Aiza/published_papers/123
            continue

        paper_key = url.rstrip("/").split("/")[-1] or url
        title = clean_text(detail.get("title") or "")
        abstract = clean_text(detail.get("abstract") or "")
        if title and abstract:
            text = f"{title}\n\n{abstract}"
        else:
            text = title or abstract

        papers.append(
            {
                "id": f"paper_{paper_key}",
                "author_id": author_id,
                "text": text,
                "keywords": split_keywords(detail.get("keywords")),
            }
        )

    papers.sort(key=lambda x: x["id"])
    return papers


def is_empty_professor(prof: dict) -> bool:
    """True when both text and keywords are completely missing."""
    return not prof.get("text") and not prof.get("keywords")


def main() -> None:
    # Touch index so the pipeline documents the declared input dependency.
    researchers_index = load_json(RESEARCHERS_INDEX)
    remp = load_json(REMP_FACULTY)
    ridb = load_json(RIDB_FACULTY)
    paper_details = load_json(REMP_PAPERS)
    attr_keywords = load_attribute_keywords(SYLLABUS_ATTR)

    professors_all, name_to_id, paper_url_to_author = build_professors(remp, ridb)

    excluded = [p for p in professors_all if is_empty_professor(p)]
    excluded_ids = [p["id"] for p in excluded]
    professors = [p for p in professors_all if not is_empty_professor(p)]

    courses = build_courses(name_to_id, attr_keywords)
    papers = build_papers(paper_details, paper_url_to_author)

    payload = {
        "professors": professors,
        "courses": courses,
        "papers": papers,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print("=== 01_build_analysis_data ===")
    print(f"researchers_index entries : {len(researchers_index)}")
    print(f"output                    : {OUT_PATH}")
    print(f"有効な教員数              : {len(professors)}")
    print(f"除外した教員数            : {len(excluded_ids)}")
    if excluded_ids:
        print("除外した教員ID:")
        for pid in excluded_ids:
            print(f"  - {pid}")
    print(f"講義数                    : {len(courses)}")
    multi_prof = sum(1 for c in courses if len(c.get("instructor_ids") or []) > 1)
    multi_url = sum(1 for c in courses if len(c.get("syllabus_urls") or []) > 1)
    multi_code = sum(1 for c in courses if len(c.get("timetable_codes") or []) > 1)
    print(f"  うち複数教員統合          : {multi_prof}")
    print(f"  うち複数シラバスURL       : {multi_url}")
    print(f"  うち複数時間割コード統合  : {multi_code}")
    print(f"papers                    : {len(papers)}")


if __name__ == "__main__":
    main()
