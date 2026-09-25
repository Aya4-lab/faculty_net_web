"""
04 — Course display tables + professor↔course (taught_by) edges.

Separation:
  Analysis  — course 3D coords come from joint UMAP in 03_embed_umap_fields.py
              (syllabus text embeddings). This script does NOT recompute positions.
  Display   — course_nodes.csv carries UI fields (name, syllabus_url[s])
  Relation  — edges_professor_course.csv (type=taught_by)

Inputs:
  data/analysis/analysis_data.json              (course ids + instructor_ids)
  data/analysis/course_coords.json              (from 03 joint UMAP)
  data/analysis/professor_coords_with_fields.json  (membership check)
  data/raw/syllabus/all_basic_info.csv          (course names)
  data/raw/syllabus/kanazawa_syllabus.csv       (full DetailMain URLs w/ lct_idx)
  data/display/display_data.json                (optional name/url fallback)

Outputs:
  data/analysis/course_nodes.csv
  data/analysis/edges_professor_course.csv
  data/analysis/course_coords.json   (enriched name/instructor; keeps UMAP positions)

Usage:
  python pipeline/04_build_course_relations.py
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from syllabus_meta import (
    join_urls,
    load_syllabus_detail_urls,
    load_syllabus_titles,
    split_urls,
    syllabus_url_for_code,
)

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
ANALYSIS = ROOT / "data" / "analysis"
DISPLAY = ROOT / "data" / "display"

ANALYSIS_PATH = ANALYSIS / "analysis_data.json"
PROF_COORDS_PATH = ANALYSIS / "professor_coords_with_fields.json"
COURSE_COORDS_PATH = ANALYSIS / "course_coords.json"
DISPLAY_PATH = DISPLAY / "display_data.json"

COURSE_NODES_CSV = ANALYSIS / "course_nodes.csv"
EDGES_CSV = ANALYSIS / "edges_professor_course.csv"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def timetable_code_from_course_id(course_id: str) -> str:
    raw = course_id.removeprefix("course_")
    return raw.split("__", 1)[0]


def instructor_ids_of(rec: dict) -> list[str]:
    ids: list[str] = []
    for src in (rec.get("instructor_ids"), [rec.get("instructor_id")]):
        for item in src or []:
            iid = (item or "").strip()
            if iid and iid not in ids:
                ids.append(iid)
    return ids


def codes_of(rec: dict, cid: str) -> list[str]:
    codes = [c.strip() for c in (rec.get("timetable_codes") or []) if str(c).strip()]
    if codes:
        return codes
    return [timetable_code_from_course_id(cid)]


def resolve_syllabus_urls(rec: dict, codes: list[str], url_by_code: dict[str, str]) -> list[str]:
    urls: list[str] = []
    for u in list(rec.get("syllabus_urls") or []) + split_urls(rec.get("syllabus_url")):
        u = (u or "").strip()
        if u and u not in urls:
            urls.append(u)
    for code in codes:
        u = syllabus_url_for_code(code, url_by_code)
        if u and u not in urls:
            urls.append(u)
    return urls


def load_display_course_meta() -> dict[str, dict]:
    if not DISPLAY_PATH.exists():
        return {}
    out: dict[str, dict] = {}
    for node in (load_json(DISPLAY_PATH).get("nodes") or []):
        if node.get("type") != "course":
            continue
        out[node["id"]] = {
            "name": (node.get("name") or "").strip(),
            "syllabus_url": (node.get("syllabus_url") or node.get("url") or "").strip(),
            "syllabus_urls": list(node.get("syllabus_urls") or split_urls(node.get("syllabus_url"))),
            "instructor_id": (node.get("instructor_id") or "").strip(),
            "instructor_ids": list(node.get("instructor_ids") or []),
        }
    return out


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def main() -> None:
    if not ANALYSIS_PATH.exists():
        raise SystemExit(f"Missing {ANALYSIS_PATH}")
    if not COURSE_COORDS_PATH.exists():
        raise SystemExit(
            f"Missing {COURSE_COORDS_PATH}. "
            "Run pipeline/03_embed_umap_fields.py first "
            "(joint UMAP writes syllabus-based course coords)."
        )
    if not PROF_COORDS_PATH.exists():
        raise SystemExit(
            f"Missing {PROF_COORDS_PATH}. Run pipeline/03_embed_umap_fields.py first."
        )

    analysis = load_json(ANALYSIS_PATH)
    course_coords = load_json(COURSE_COORDS_PATH)
    prof_coords = load_json(PROF_COORDS_PATH)
    titles = load_syllabus_titles()
    url_by_code = load_syllabus_detail_urls()
    display_meta = load_display_course_meta()

    course_rows: list[dict] = []
    edges: list[dict] = []
    coords_out: dict[str, dict] = {}
    skipped_no_coord = 0
    skipped_no_prof = 0
    seen_ids: set[str] = set()
    seen_edges: set[tuple[str, str]] = set()

    for rec in analysis.get("courses") or []:
        cid = (rec.get("id") or "").strip()
        instructor_ids = instructor_ids_of(rec)
        if not cid or not instructor_ids:
            continue
        if cid in seen_ids:
            continue
        seen_ids.add(cid)
        if cid not in course_coords:
            skipped_no_coord += 1
            continue

        codes = codes_of(rec, cid)
        primary_code = codes[0]
        dmeta = display_meta.get(cid) or {}
        umap_row = course_coords.get(cid) or {}
        name = (
            dmeta.get("name")
            or titles.get(primary_code)
            or umap_row.get("name")
            or cid
        ).strip()
        for code in codes:
            if not name or name == cid:
                name = (titles.get(code) or name).strip()

        syllabus_urls = resolve_syllabus_urls(rec, codes, url_by_code)
        if not syllabus_urls:
            syllabus_urls = split_urls(dmeta.get("syllabus_url")) + list(
                dmeta.get("syllabus_urls") or []
            )
        # de-dupe preserving order
        deduped: list[str] = []
        for u in syllabus_urls:
            if u and u not in deduped:
                deduped.append(u)
        syllabus_urls = deduped
        syllabus_url = syllabus_urls[0] if syllabus_urls else ""

        position = umap_row.get("position")
        if not isinstance(position, list) or len(position) != 3:
            skipped_no_coord += 1
            continue

        course_rows.append(
            {
                "course_id": cid,
                "course_name": name,
                "instructor_id": instructor_ids[0],
                "instructor_ids": "|".join(instructor_ids),
                "timetable_code": primary_code,
                "timetable_codes": "|".join(codes),
                "syllabus_url": syllabus_url,
                "syllabus_urls": join_urls(syllabus_urls),
            }
        )
        for instructor_id in instructor_ids:
            if instructor_id not in prof_coords:
                skipped_no_prof += 1
                continue
            key = (instructor_id, cid)
            if key in seen_edges:
                continue
            seen_edges.add(key)
            edges.append(
                {
                    "professor_id": instructor_id,
                    "course_id": cid,
                    "type": "taught_by",
                }
            )

        coords_out[cid] = {
            "instructor_id": instructor_ids[0],
            "instructor_ids": instructor_ids,
            "name": name,
            "position": [
                round(float(position[0]), 6),
                round(float(position[1]), 6),
                round(float(position[2]), 6),
            ],
            "source": umap_row.get("source") or "joint_umap_syllabus",
        }

    course_rows.sort(key=lambda r: r["course_id"])
    edges.sort(key=lambda e: (e["professor_id"], e["course_id"]))

    write_csv(
        COURSE_NODES_CSV,
        course_rows,
        [
            "course_id",
            "course_name",
            "instructor_id",
            "instructor_ids",
            "timetable_code",
            "timetable_codes",
            "syllabus_url",
            "syllabus_urls",
        ],
    )
    write_csv(
        EDGES_CSV,
        edges,
        ["professor_id", "course_id", "type"],
    )
    COURSE_COORDS_PATH.write_text(
        json.dumps(coords_out, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    per_prof: dict[str, int] = {}
    for e in edges:
        per_prof[e["professor_id"]] = per_prof.get(e["professor_id"], 0) + 1

    multi_prof = sum(1 for r in course_rows if "|" in (r.get("instructor_ids") or ""))
    multi_url = sum(1 for r in course_rows if "|" in (r.get("syllabus_urls") or ""))
    with_lct_idx = sum(1 for r in course_rows if "lct_idx=" in (r.get("syllabus_url") or ""))

    print("=== 04_build_course_relations ===")
    print(f"course_nodes.csv            : {COURSE_NODES_CSV}")
    print(f"edges_professor_course.csv  : {EDGES_CSV}")
    print(f"course_coords.json          : {COURSE_COORDS_PATH}")
    print(f"courses                     : {len(course_rows)}")
    print(f"taught_by edges             : {len(edges)}")
    print(f"professors with >=1 course  : {len(per_prof)}")
    print(f"multi-instructor courses    : {multi_prof}")
    print(f"multi-URL courses           : {multi_url}")
    print(f"syllabus_url has lct_idx    : {with_lct_idx}/{len(course_rows)}")
    print(f"skipped (no UMAP coords)    : {skipped_no_coord}")
    print(f"skipped edge (no prof)      : {skipped_no_prof}")
    if per_prof:
        counts = sorted(per_prof.values())
        print(
            f"courses/prof                : "
            f"min={counts[0]} median={counts[len(counts)//2]} max={counts[-1]}"
        )


if __name__ == "__main__":
    main()
