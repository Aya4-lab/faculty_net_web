"""
02 — Build field tree + professor→field membership edges.

Inputs (Raw):
  data/raw/ridb/seeds_taxonomy.json
  data/raw/ridb/ridb_yugo_faculty_ja.json
  data/raw/ridb/seeds_membership.json   (optional; preferred when present)

Outputs (Analysis / Relation):
  data/analysis/field_nodes.csv
  data/analysis/edges_professor_field.csv
  data/analysis/field_tree.json         (same info, nested)

Membership priority:
  1. seeds_membership.json (official seeds page listings), intersected with our faculty
  2. fallback: exact match of research.specialty tokens to seeds field names
     (+ small alias table)

Usage:
  python pipeline/02_build_field_tree.py
  python pipeline/02_build_field_tree.py --specialty-only
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_RIDB = ROOT / "data" / "raw" / "ridb"
ANALYSIS = ROOT / "data" / "analysis"

TAXONOMY_PATH = RAW_RIDB / "seeds_taxonomy.json"
FACULTY_PATH = RAW_RIDB / "ridb_yugo_faculty_ja.json"
MEMBERSHIP_PATH = RAW_RIDB / "seeds_membership.json"

FIELD_NODES_CSV = ANALYSIS / "field_nodes.csv"
EDGES_CSV = ANALYSIS / "edges_professor_field.csv"
FIELD_TREE_JSON = ANALYSIS / "field_tree.json"

SPECIALTY_SPLIT_RE = re.compile(r"[、,，;/／|]+")

# Clear renames only (no speculative fuzzy matches).
SPECIALTY_ALIASES: dict[str, str] = {
    "医用生体工学 生体材料学": "生体医工学・生体材料学",
    "医用生体工学・生体材料学": "生体医工学・生体材料学",
    "環境政策": "環境政策・環境社会システム",
    "心身医学": "内科学一般（含心身医学）",
    "生体生命情報学": "生命・健康・医療情報学",
    "基礎ゲノム科学": "ゲノム生物学",
    "応用ゲノム科学": "ゲノム医科学",
    "財政学 公共経済学": "財政・公共経済",
    "科学教育関連": "科学教育",
    "環境農学": "環境農学（含ランドスケープ科学）",
}


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def split_specialty(raw: str) -> list[str]:
    if not raw:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for part in SPECIALTY_SPLIT_RE.split(raw):
        token = part.strip()
        if not token:
            continue
        token = SPECIALTY_ALIASES.get(token, token)
        if token not in seen:
            seen.add(token)
            out.append(token)
    return out


def field_id_from_cat(cat: int | None, name: str) -> str:
    if cat is not None:
        return f"field_{cat}"
    slug = re.sub(r"\W+", "_", name).strip("_")
    return f"field_{slug}"


def major_id_from_name(name: str) -> str:
    slug = re.sub(r"\W+", "_", name).strip("_")
    return f"major_{slug}"


def build_taxonomy_indexes(taxonomy: dict) -> tuple[list[dict], dict[str, dict]]:
    """Return flat field rows and name→field lookup."""
    fields: list[dict] = []
    by_name: dict[str, dict] = {}
    for major in taxonomy.get("majors") or []:
        major_name = major["name"]
        mid = major_id_from_name(major_name)
        for field in major.get("fields") or []:
            cat = field.get("cat")
            fid = field_id_from_cat(cat, field["name"])
            row = {
                "id": fid,
                "level": "field",
                "name": field["name"],
                "parent_id": mid,
                "major_name": major_name,
                "cat": cat,
                "seeds_count": field.get("seeds_count", 0),
                "url": field.get("url") or "",
            }
            fields.append(row)
            by_name[field["name"]] = row
    return fields, by_name


def build_major_nodes(taxonomy: dict) -> list[dict]:
    nodes: list[dict] = []
    for major in taxonomy.get("majors") or []:
        nodes.append(
            {
                "id": major_id_from_name(major["name"]),
                "level": "major",
                "name": major["name"],
                "parent_id": "",
                "major_name": major["name"],
                "cat": "",
                "seeds_count": sum(f.get("seeds_count", 0) for f in major.get("fields") or []),
                "url": "",
            }
        )
    return nodes


def edges_from_membership(
    membership: dict,
    our_prof_ids: set[str],
    field_by_name: dict[str, dict],
) -> list[dict]:
    edges: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for entry in (membership.get("fields") or {}).values():
        field_name = entry.get("name") or ""
        field = field_by_name.get(field_name)
        if not field:
            continue
        for ridb_id in entry.get("yugo_professor_ridb_ids") or entry.get(
            "professor_ridb_ids"
        ) or []:
            prof_id = f"prof_{ridb_id}"
            if prof_id not in our_prof_ids:
                continue
            key = (prof_id, field["id"])
            if key in seen:
                continue
            seen.add(key)
            edges.append(
                {
                    "source": prof_id,
                    "target": field["id"],
                    "type": "belongs_to_field",
                    "method": "seeds_page",
                }
            )
    return edges


def edges_from_specialty(
    faculty: list[dict],
    our_prof_ids: set[str],
    field_by_name: dict[str, dict],
    existing: set[tuple[str, str]],
) -> tuple[list[dict], list[str]]:
    edges: list[dict] = []
    unmatched: list[str] = []
    unmatched_seen: set[str] = set()

    for row in faculty:
        basic = row.get("basic") or {}
        ridb_id = str(basic.get("id") or "").strip()
        if not ridb_id:
            continue
        prof_id = f"prof_{ridb_id}"
        if prof_id not in our_prof_ids:
            continue
        specialty = (row.get("research") or {}).get("specialty") or ""
        for token in split_specialty(specialty):
            field = field_by_name.get(token)
            if not field:
                if token not in unmatched_seen:
                    unmatched_seen.add(token)
                    unmatched.append(token)
                continue
            key = (prof_id, field["id"])
            if key in existing:
                continue
            existing.add(key)
            edges.append(
                {
                    "source": prof_id,
                    "target": field["id"],
                    "type": "belongs_to_field",
                    "method": "specialty",
                }
            )
    return edges, unmatched


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def main() -> None:
    parser = argparse.ArgumentParser(description="Step 1: field tree + membership edges")
    parser.add_argument(
        "--specialty-only",
        action="store_true",
        help="Ignore seeds_membership.json; use specialty matching only",
    )
    args = parser.parse_args()

    if not TAXONOMY_PATH.exists():
        raise SystemExit(
            f"Missing {TAXONOMY_PATH}. Run scraping/07_fetch_seeds_taxonomy.py first."
        )

    taxonomy = load_json(TAXONOMY_PATH)
    faculty = load_json(FACULTY_PATH)
    our_prof_ids = {
        f"prof_{str((row.get('basic') or {}).get('id') or '').strip()}"
        for row in faculty
        if (row.get("basic") or {}).get("id")
    }

    field_rows, field_by_name = build_taxonomy_indexes(taxonomy)
    major_rows = build_major_nodes(taxonomy)

    edges: list[dict] = []
    if not args.specialty_only and MEMBERSHIP_PATH.exists():
        membership = load_json(MEMBERSHIP_PATH)
        edges.extend(edges_from_membership(membership, our_prof_ids, field_by_name))
        print(f"edges from seeds_membership: {len(edges)}")
    elif not args.specialty_only:
        print(f"(no {MEMBERSHIP_PATH.name}; specialty fallback will fill edges)")

    existing = {(e["source"], e["target"]) for e in edges}
    specialty_edges, unmatched = edges_from_specialty(
        faculty, our_prof_ids, field_by_name, existing
    )
    edges.extend(specialty_edges)

    # Keep only fields/majors that have at least one member (plus their parents).
    used_field_ids = {e["target"] for e in edges}
    used_fields = [f for f in field_rows if f["id"] in used_field_ids]
    used_major_ids = {f["parent_id"] for f in used_fields}
    used_majors = [m for m in major_rows if m["id"] in used_major_ids]

    # Annotate member counts on nodes we keep (and keep full tree in JSON).
    members_by_field: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        members_by_field[edge["target"]].append(edge["source"])

    node_rows: list[dict] = []
    for major in major_rows:
        child_fields = [f for f in field_rows if f["parent_id"] == major["id"]]
        member_count = sum(len(members_by_field[f["id"]]) for f in child_fields)
        node_rows.append(
            {
                **major,
                "member_count": member_count,
                "active": int(major["id"] in used_major_ids),
            }
        )
    for field in field_rows:
        node_rows.append(
            {
                **field,
                "member_count": len(members_by_field[field["id"]]),
                "active": int(field["id"] in used_field_ids),
            }
        )

    write_csv(
        FIELD_NODES_CSV,
        node_rows,
        [
            "id",
            "level",
            "name",
            "parent_id",
            "major_name",
            "cat",
            "seeds_count",
            "member_count",
            "active",
            "url",
        ],
    )
    write_csv(
        EDGES_CSV,
        edges,
        ["source", "target", "type", "method"],
    )

    tree_payload = {
        "majors": [
            {
                "id": m["id"],
                "name": m["name"],
                "member_count": next(
                    n["member_count"] for n in node_rows if n["id"] == m["id"]
                ),
                "fields": [
                    {
                        "id": f["id"],
                        "name": f["name"],
                        "cat": f["cat"],
                        "url": f["url"],
                        "seeds_count": f["seeds_count"],
                        "member_count": len(members_by_field[f["id"]]),
                        "professor_ids": members_by_field[f["id"]],
                    }
                    for f in field_rows
                    if f["parent_id"] == m["id"]
                ],
            }
            for m in major_rows
        ],
        "edges_professor_field": edges,
        "unmatched_specialty_tokens": unmatched,
    }
    FIELD_TREE_JSON.parent.mkdir(parents=True, exist_ok=True)
    FIELD_TREE_JSON.write_text(
        json.dumps(tree_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    n_prof = len({e["source"] for e in edges})
    print("=== 02_build_field_tree ===")
    print(f"field_nodes.csv           : {FIELD_NODES_CSV}")
    print(f"edges_professor_field.csv : {EDGES_CSV}")
    print(f"field_tree.json           : {FIELD_TREE_JSON}")
    print(f"taxonomy majors/fields    : {len(major_rows)} / {len(field_rows)}")
    print(f"active majors/fields      : {len(used_majors)} / {len(used_fields)}")
    print(f"professors with ≥1 field  : {n_prof} / {len(our_prof_ids)}")
    print(f"belongs_to_field edges    : {len(edges)}")
    by_method: dict[str, int] = defaultdict(int)
    for e in edges:
        by_method[e["method"]] += 1
    for method, count in sorted(by_method.items()):
        print(f"  method={method}: {count}")
    if unmatched:
        print(f"unmatched specialty tokens ({len(unmatched)}):")
        for token in unmatched:
            print(f"  - {token}")


if __name__ == "__main__":
    main()
