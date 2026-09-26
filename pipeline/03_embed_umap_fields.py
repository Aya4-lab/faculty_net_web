"""
03 — Embed texts + joint UMAP (professors / fields / courses).

Uses analysis_data.json texts (same embedding model as 05_export_display_data.py).

Vector rules:
  - professor / course vector = embed(text [SEP] キーワード: ...)
      (falls back to keywords-only or text-only when the other is missing)
  - research-field vector     = ensemble of
        (a) embed(大分野 / 小分野名 [SEP] キーワード: field_keywords)
        (b) mean of member professors' embeddings
  - major-field vector        = ensemble of
        (a) embed(大分野名 [SEP] キーワード: child keywords)
        (b) mean of child research-field vectors
  - then UMAP jointly on [professors + research fields + majors + courses]

Outputs (Analysis):
  data/analysis/professor_embeddings.npz
  data/analysis/field_embeddings.npz
  data/analysis/course_embeddings.npz
  data/analysis/field_coords.json
  data/analysis/professor_coords_with_fields.json
  data/analysis/course_coords.json

Optional:
  --update-display  merge field nodes/edges into data/display/display_data.json
                    and replace professor (+ course) positions with joint UMAP

Usage:
  python pipeline/03_embed_umap_fields.py
  python pipeline/03_embed_umap_fields.py --update-display
  python pipeline/03_embed_umap_fields.py --reuse-embeddings
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
ANALYSIS = ROOT / "data" / "analysis"
DISPLAY = ROOT / "data" / "display"
RAW = ROOT / "data" / "raw"

ANALYSIS_PATH = ANALYSIS / "analysis_data.json"
EDGES_CSV = ANALYSIS / "edges_professor_field.csv"
FIELD_NODES_CSV = ANALYSIS / "field_nodes.csv"
FIELD_TREE_JSON = ANALYSIS / "field_tree.json"
SYLLABUS_BASIC = RAW / "syllabus" / "all_basic_info.csv"

PROF_EMB_PATH = ANALYSIS / "professor_embeddings.npz"
FIELD_EMB_PATH = ANALYSIS / "field_embeddings.npz"
COURSE_EMB_PATH = ANALYSIS / "course_embeddings.npz"
FIELD_COORDS_PATH = ANALYSIS / "field_coords.json"
PROF_COORDS_PATH = ANALYSIS / "professor_coords_with_fields.json"
COURSE_COORDS_PATH = ANALYSIS / "course_coords.json"
DISPLAY_PATH = DISPLAY / "display_data.json"

from syllabus_meta import (
    load_syllabus_detail_urls,
    load_syllabus_titles,
    syllabus_url_for_code,
)

MODEL_NAME = "intfloat/multilingual-e5-base"
BATCH_SIZE = 32
RANDOM_STATE = 42
# Tuned for ~800 points (professors + fields + courses)
UMAP_N_NEIGHBORS = 15
UMAP_MIN_DIST = 0.10
UMAP_SPREAD = 1.15
COORD_TARGET = 28.0
# Weight of field/major definition embedding vs member/child mean (0–1).
# Higher → field anchors stay stable even when cross-cutting faculty join.
FIELD_DEF_WEIGHT = 0.65
MAJOR_DEF_WEIGHT = 0.55


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def re_split_keywords(raw: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"[|、,，;/／\n]+", raw or ""):
        token = part.strip()
        if not token:
            continue
        key = token.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(token)
    return out


def _clean_keywords(raw) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        # field_nodes.csv stores keywords as pipe-joined
        return re_split_keywords(raw)
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        token = str(item).strip()
        if not token:
            continue
        key = token.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(token)
    return out


def embed_source_text(record: dict) -> str | None:
    """
    Build embedding input: prose + keywords (when both exist).
    Missing keywords → prose only; missing prose → keywords only.
    """
    text = (record.get("text") or "").strip()
    keywords = _clean_keywords(record.get("keywords"))
    # Prefer explicit subfield_keywords appended if not already in keywords
    sub_kw = _clean_keywords(record.get("subfield_keywords"))
    if sub_kw:
        seen = {k.casefold() for k in keywords}
        for kw in sub_kw:
            if kw.casefold() not in seen:
                keywords.append(kw)
                seen.add(kw.casefold())

    if text and keywords:
        return f"{text} [SEP] キーワード: {', '.join(keywords)}"
    if text:
        return text
    if keywords:
        return f"キーワード: {', '.join(keywords)}"
    return None


def field_definition_text(row: dict) -> str:
    """Definition text for a field/major node (name + scraped keywords)."""
    name = (row.get("name") or "").strip()
    major = (row.get("major_name") or "").strip()
    keywords = _clean_keywords(row.get("keywords"))
    if row.get("level") == "field" and major and name:
        head = f"{major} / {name}"
    else:
        head = name or major
    if head and keywords:
        return f"{head} [SEP] キーワード: {', '.join(keywords)}"
    if head:
        return head
    if keywords:
        return f"キーワード: {', '.join(keywords)}"
    return ""


def encode_texts(texts: list[str]) -> np.ndarray:
    from sentence_transformers import SentenceTransformer

    print(f"Loading model: {MODEL_NAME}")
    model = SentenceTransformer(MODEL_NAME)
    passages = [f"passage: {t}" for t in texts]
    print(f"Encoding {len(passages)} texts (batch_size={BATCH_SIZE}) ...")
    vectors = model.encode(
        passages,
        batch_size=BATCH_SIZE,
        show_progress_bar=True,
        normalize_embeddings=True,
    )
    return np.asarray(vectors, dtype=np.float64)


def l2_normalize(vec: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vec))
    if norm > 1e-12:
        return vec / norm
    return vec


def ensemble_vectors(
    def_vec: np.ndarray | None,
    member_vec: np.ndarray | None,
    *,
    def_weight: float,
) -> np.ndarray | None:
    """Weighted blend of definition and member/child mean; L2-normalized."""
    if def_vec is None and member_vec is None:
        return None
    if def_vec is None:
        return l2_normalize(np.asarray(member_vec, dtype=np.float64))
    if member_vec is None:
        return l2_normalize(np.asarray(def_vec, dtype=np.float64))
    w = float(np.clip(def_weight, 0.0, 1.0))
    blended = w * np.asarray(def_vec, dtype=np.float64) + (1.0 - w) * np.asarray(
        member_vec, dtype=np.float64
    )
    return l2_normalize(blended)


def normalize_coords(coords: np.ndarray, target: float = COORD_TARGET) -> np.ndarray:
    centered = coords - coords.mean(axis=0)
    max_abs = float(np.max(np.abs(centered))) if centered.size else 0.0
    if max_abs < 1e-9:
        return centered
    return (centered * (target / max_abs)).astype(float)


def run_umap(embeddings: np.ndarray) -> np.ndarray:
    from umap import UMAP

    n = embeddings.shape[0]
    n_neighbors = min(UMAP_N_NEIGHBORS, max(2, n - 1))
    print(
        "UMAP: "
        f"n={n}, n_neighbors={n_neighbors}, min_dist={UMAP_MIN_DIST}, "
        f"spread={UMAP_SPREAD}, metric=cosine"
    )
    reducer = UMAP(
        n_components=3,
        n_neighbors=n_neighbors,
        metric="cosine",
        random_state=RANDOM_STATE,
        min_dist=UMAP_MIN_DIST,
        spread=UMAP_SPREAD,
    )
    coords = np.asarray(reducer.fit_transform(embeddings), dtype=float)
    print(f"UMAP done: range=[{coords.min():.3f}, {coords.max():.3f}]")
    return coords


def run_pca(embeddings: np.ndarray) -> np.ndarray:
    """Fallback when umap-learn / numba is unavailable on this machine."""
    from sklearn.decomposition import PCA

    print(f"PCA fallback: n={embeddings.shape[0]}, n_components=3")
    reducer = PCA(n_components=3, random_state=RANDOM_STATE)
    return np.asarray(reducer.fit_transform(embeddings), dtype=float)


def reduce_to_3d(embeddings: np.ndarray, method: str) -> tuple[np.ndarray, str]:
    """Returns (coords, method_used)."""
    if method == "pca":
        return run_pca(embeddings), "pca"
    if method != "umap":
        raise SystemExit(f"Unknown --method {method}")
    try:
        return run_umap(embeddings), "umap"
    except Exception as exc:  # noqa: BLE001
        print(f"UMAP unavailable ({type(exc).__name__}: {exc}); falling back to PCA.")
        print(
            "Tip: pin numba/llvmlite per requirements.txt if Windows blocks numba DLLs."
        )
        return run_pca(embeddings), "pca"


def load_membership_edges() -> list[dict]:
    if not EDGES_CSV.exists():
        raise SystemExit(f"Missing {EDGES_CSV}. Run pipeline/02_build_field_tree.py first.")
    with EDGES_CSV.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_field_nodes() -> list[dict]:
    with FIELD_NODES_CSV.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def timetable_code_from_course_id(course_id: str) -> str:
    raw = course_id.removeprefix("course_")
    return raw.split("__", 1)[0]


def load_syllabus_names() -> dict[str, str]:
    """timetable code -> course title."""
    return load_syllabus_titles()


def course_display_name(rec: dict, syllabus_names: dict[str, str], cid: str) -> str:
    codes = [c.strip() for c in (rec.get("timetable_codes") or []) if str(c).strip()]
    if not codes:
        codes = [timetable_code_from_course_id(cid)]
    for code in codes:
        name = syllabus_names.get(code) or ""
        if name:
            return name
    return cid


def course_syllabus_url(rec: dict, url_by_code: dict[str, str], cid: str) -> str:
    urls = [u.strip() for u in (rec.get("syllabus_urls") or []) if str(u).strip()]
    if urls:
        return urls[0]
    single = (rec.get("syllabus_url") or "").strip()
    if single:
        return single
    codes = [c.strip() for c in (rec.get("timetable_codes") or []) if str(c).strip()]
    if not codes:
        codes = [timetable_code_from_course_id(cid)]
    for code in codes:
        url = syllabus_url_for_code(code, url_by_code)
        if url:
            return url
    return ""


def _load_cached_embeddings(
    path: Path, usable_ids: list[str]
) -> tuple[list[str], np.ndarray] | None:
    if not path.exists():
        return None
    cached = np.load(path, allow_pickle=True)
    cached_ids = [str(x) for x in cached["ids"].tolist()]
    cached_vecs = np.asarray(cached["vectors"], dtype=np.float64)
    by_id = dict(zip(cached_ids, cached_vecs))
    ids = [i for i in usable_ids if i in by_id]
    if len(ids) != len(usable_ids):
        return None
    return ids, np.stack([by_id[i] for i in ids])


def load_or_compute_entity_embeddings(
    records: list[dict],
    *,
    cache_path: Path,
    reuse: bool,
    label: str,
) -> tuple[list[str], np.ndarray]:
    usable = []
    seen: set[str] = set()
    for rec in records:
        rid = (rec.get("id") or "").strip()
        if not rid or rid in seen:
            continue
        source = embed_source_text(rec)
        if source is None:
            continue
        seen.add(rid)
        usable.append({"id": rid, "source_text": source})

    usable_ids = [p["id"] for p in usable]
    if reuse:
        hit = _load_cached_embeddings(cache_path, usable_ids)
        if hit is not None:
            print(f"Reusing cached {label} embeddings: {cache_path}")
            return hit
        if cache_path.exists():
            print(f"Cache incomplete for {label}; re-encoding.")

    vectors = encode_texts([p["source_text"] for p in usable])
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        cache_path, ids=np.array(usable_ids, dtype=object), vectors=vectors
    )
    print(f"saved {label} embeddings: {cache_path} ({len(usable_ids)})")
    return usable_ids, vectors


def build_field_embeddings(
    prof_ids: list[str],
    prof_vecs: np.ndarray,
    edges: list[dict],
    field_nodes: list[dict],
) -> tuple[list[dict], np.ndarray]:
    """
    Field vector = ensemble(definition embed, mean of members).
    Major vector = ensemble(definition embed, mean of child fields).
    """
    prof_index = {pid: i for i, pid in enumerate(prof_ids)}
    members: dict[str, list[int]] = defaultdict(list)
    for edge in edges:
        pid = edge["source"]
        fid = edge["target"]
        if pid in prof_index:
            members[fid].append(prof_index[pid])

    field_rows = [n for n in field_nodes if n["level"] == "field"]
    major_rows = [n for n in field_nodes if n["level"] == "major"]

    # Precompute definition texts for active fields (have ≥1 embedded member)
    active_field_rows: list[dict] = []
    field_def_texts: list[str] = []
    for row in field_rows:
        idxs = members.get(row["id"]) or []
        if not idxs:
            continue
        def_text = field_definition_text(row)
        if not def_text:
            def_text = (row.get("name") or row["id"]).strip()
        active_field_rows.append(row)
        field_def_texts.append(def_text)

    if not active_field_rows:
        raise SystemExit("No field embeddings could be built (no membership overlap).")

    # Major definitions for majors that will have active children
    pending_major_ids = {
        row["parent_id"] for row in active_field_rows if row.get("parent_id")
    }
    active_major_rows = [n for n in major_rows if n["id"] in pending_major_ids]
    major_def_texts = []
    for row in active_major_rows:
        def_text = field_definition_text(row)
        if not def_text:
            def_text = (row.get("name") or row["id"]).strip()
        major_def_texts.append(def_text)

    print(
        f"Encoding field/major definitions: "
        f"{len(field_def_texts)} fields + {len(major_def_texts)} majors "
        f"(def_weight field={FIELD_DEF_WEIGHT}, major={MAJOR_DEF_WEIGHT})"
    )
    all_def_texts = field_def_texts + major_def_texts
    all_def_vecs = encode_texts(all_def_texts)
    field_def_vecs = all_def_vecs[: len(field_def_texts)]
    major_def_by_id = {
        row["id"]: all_def_vecs[len(field_def_texts) + i]
        for i, row in enumerate(active_major_rows)
    }

    active_fields: list[dict] = []
    field_vecs: list[np.ndarray] = []
    field_id_to_vec: dict[str, np.ndarray] = {}

    for i, row in enumerate(active_field_rows):
        idxs = members.get(row["id"]) or []
        member_mean = prof_vecs[idxs].mean(axis=0) if idxs else None
        vec = ensemble_vectors(
            field_def_vecs[i],
            member_mean,
            def_weight=FIELD_DEF_WEIGHT,
        )
        if vec is None:
            continue
        keywords = _clean_keywords(row.get("keywords"))
        active_fields.append(
            {
                "id": row["id"],
                "level": "field",
                "name": row["name"],
                "parent_id": row["parent_id"],
                "member_count": len(idxs),
                "keywords": keywords,
                "vector_source": "definition+members",
            }
        )
        field_vecs.append(vec)
        field_id_to_vec[row["id"]] = vec

    children_by_major: dict[str, list[str]] = defaultdict(list)
    for row in active_fields:
        children_by_major[row["parent_id"]].append(row["id"])

    active_majors: list[dict] = []
    major_vecs: list[np.ndarray] = []
    for row in active_major_rows:
        child_ids = children_by_major.get(row["id"]) or []
        if not child_ids:
            continue
        child_mean = np.stack([field_id_to_vec[cid] for cid in child_ids]).mean(axis=0)
        vec = ensemble_vectors(
            major_def_by_id.get(row["id"]),
            child_mean,
            def_weight=MAJOR_DEF_WEIGHT,
        )
        if vec is None:
            continue
        keywords = _clean_keywords(row.get("keywords"))
        active_majors.append(
            {
                "id": row["id"],
                "level": "major",
                "name": row["name"],
                "parent_id": "",
                "member_count": sum(
                    next(f["member_count"] for f in active_fields if f["id"] == cid)
                    for cid in child_ids
                ),
                "child_field_ids": child_ids,
                "keywords": keywords,
                "vector_source": "definition+children",
            }
        )
        major_vecs.append(vec)

    meta = active_fields + active_majors
    if not meta:
        raise SystemExit("No field embeddings could be built (no membership overlap).")
    vectors = np.stack(field_vecs + major_vecs)
    return meta, vectors


def update_display(
    *,
    prof_ids: list[str],
    prof_coords: np.ndarray,
    field_meta: list[dict],
    field_coords: np.ndarray,
    course_ids: list[str],
    course_coords: np.ndarray,
    course_meta: dict[str, dict],
    edges: list[dict],
    analysis_professors: list[dict] | None = None,
) -> None:
    if not DISPLAY_PATH.exists():
        raise SystemExit(f"Missing {DISPLAY_PATH}")

    display = load_json(DISPLAY_PATH)
    prof_analysis = {
        p["id"]: p for p in (analysis_professors or []) if p.get("id")
    }
    prof_pos = {
        pid: [
            round(float(prof_coords[i, 0]), 6),
            round(float(prof_coords[i, 1]), 6),
            round(float(prof_coords[i, 2]), 6),
        ]
        for i, pid in enumerate(prof_ids)
    }
    course_pos = {
        cid: [
            round(float(course_coords[i, 0]), 6),
            round(float(course_coords[i, 1]), 6),
            round(float(course_coords[i, 2]), 6),
        ]
        for i, cid in enumerate(course_ids)
    }

    nodes_out: list[dict] = []
    for node in display.get("nodes") or []:
        ntype = node.get("type")
        nid = str(node.get("id") or "")
        # Drop field/major nodes (by type or id prefix) so they can be rebuilt cleanly.
        # Also removes corrupted leftovers that were once rewritten as type=course.
        if ntype in {"field", "major"} or nid.startswith(("field_", "major_")):
            continue
        if ntype == "professor" and nid in prof_pos:
            analysis = prof_analysis.get(nid) or {}
            updated = {**node, "position": prof_pos[nid]}
            if analysis.get("keywords") is not None:
                updated["keywords"] = list(analysis.get("keywords") or [])
            if analysis.get("subfield_keywords") is not None:
                updated["subfield_keywords"] = list(
                    analysis.get("subfield_keywords") or []
                )
            node = updated
        elif ntype == "course" and nid in course_pos:
            node = {**node, "position": course_pos[nid]}
        nodes_out.append(node)

    existing_ids = {n["id"] for n in nodes_out}
    for cid in course_ids:
        if cid in existing_ids:
            continue
        meta = course_meta.get(cid) or {}
        nodes_out.append(
            {
                "id": cid,
                "type": "course",
                "name": meta.get("name") or cid,
                "instructor_id": meta.get("instructor_id") or "",
                "instructor_ids": list(meta.get("instructor_ids") or []),
                "timetable_codes": list(meta.get("timetable_codes") or []),
                "syllabus_url": meta.get("syllabus_url") or "",
                "syllabus_urls": list(meta.get("syllabus_urls") or []),
                "url": meta.get("syllabus_url") or "",
                "position": course_pos[cid],
            }
        )

    for i, meta in enumerate(field_meta):
        nodes_out.append(
            {
                "id": meta["id"],
                "type": meta["level"],
                "name": meta["name"],
                "parent_id": meta.get("parent_id") or "",
                "member_count": meta.get("member_count", 0),
                "keywords": list(meta.get("keywords") or []),
                "position": [
                    round(float(field_coords[i, 0]), 6),
                    round(float(field_coords[i, 1]), 6),
                    round(float(field_coords[i, 2]), 6),
                ],
            }
        )

    kept_edges = [
        e
        for e in (display.get("edges") or [])
        if e.get("type") not in {"belongs_to_field", "field_hierarchy"}
    ]
    field_ids = {m["id"] for m in field_meta}
    membership_edges = [
        {
            "source": e["source"],
            "target": e["target"],
            "type": "belongs_to_field",
            "method": e.get("method", ""),
        }
        for e in edges
        if e["target"] in field_ids and e["source"] in prof_pos
    ]
    hierarchy_edges = [
        {
            "source": m["id"],
            "target": m["parent_id"],
            "type": "field_hierarchy",
        }
        for m in field_meta
        if m["level"] == "field" and m.get("parent_id") in field_ids
    ]

    payload = {
        "nodes": nodes_out,
        "edges": kept_edges + membership_edges + hierarchy_edges,
    }
    DISPLAY_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"updated display: {DISPLAY_PATH}")
    print(
        f"  + field/major nodes={len(field_meta)}, "
        f"belongs_to_field={len(membership_edges)}, "
        f"field_hierarchy={len(hierarchy_edges)}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Joint UMAP: professors + field definition/member ensemble + courses"
        )
    )
    parser.add_argument(
        "--reuse-embeddings",
        action="store_true",
        help="Reuse professor/course embedding caches when complete",
    )
    parser.add_argument(
        "--update-display",
        action="store_true",
        help="Merge field/course positions into display_data.json",
    )
    parser.add_argument(
        "--method",
        choices=("umap", "pca"),
        default="umap",
        help="Dimensionality reduction (umap falls back to pca if numba/umap fails)",
    )
    args = parser.parse_args()

    if not ANALYSIS_PATH.exists():
        raise SystemExit(f"Missing {ANALYSIS_PATH}")
    if not FIELD_NODES_CSV.exists():
        raise SystemExit(
            f"Missing {FIELD_NODES_CSV}. Run pipeline/02_build_field_tree.py first."
        )

    analysis = load_json(ANALYSIS_PATH)
    professors = analysis.get("professors") or []
    courses = analysis.get("courses") or []
    edges = load_membership_edges()
    field_nodes = load_field_nodes()

    # Keyword-augmented texts invalidate old caches that ignored keywords.
    prof_ids, prof_vecs = load_or_compute_entity_embeddings(
        professors,
        cache_path=PROF_EMB_PATH,
        reuse=args.reuse_embeddings,
        label="professor",
    )
    course_ids, course_vecs = load_or_compute_entity_embeddings(
        courses,
        cache_path=COURSE_EMB_PATH,
        reuse=args.reuse_embeddings,
        label="course",
    )
    field_meta, field_vecs = build_field_embeddings(
        prof_ids, prof_vecs, edges, field_nodes
    )

    FIELD_EMB_PATH.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        FIELD_EMB_PATH,
        ids=np.array([m["id"] for m in field_meta], dtype=object),
        levels=np.array([m["level"] for m in field_meta], dtype=object),
        vectors=field_vecs,
    )
    print(f"saved field embeddings: {FIELD_EMB_PATH} ({len(field_meta)} nodes)")

    # Joint order: professors | fields(+majors) | courses
    joint_vecs = np.vstack([prof_vecs, field_vecs, course_vecs])
    coords_raw, method_used = reduce_to_3d(joint_vecs, args.method)
    coords = normalize_coords(coords_raw)

    n_prof = len(prof_ids)
    n_field = len(field_meta)
    prof_coords = coords[:n_prof]
    field_coords = coords[n_prof : n_prof + n_field]
    course_coords = coords[n_prof + n_field :]

    syllabus_names = load_syllabus_names()
    syllabus_urls = load_syllabus_detail_urls()
    course_by_id = {c["id"]: c for c in courses}
    course_meta: dict[str, dict] = {}
    for cid in course_ids:
        rec = course_by_id.get(cid) or {}
        course_meta[cid] = {
            "instructor_id": (rec.get("instructor_id") or "").strip(),
            "instructor_ids": list(rec.get("instructor_ids") or []),
            "name": course_display_name(rec, syllabus_names, cid),
            "syllabus_url": course_syllabus_url(rec, syllabus_urls, cid),
            "syllabus_urls": list(rec.get("syllabus_urls") or []),
            "timetable_codes": list(rec.get("timetable_codes") or []),
        }

    prof_coord_payload = {
        pid: [
            round(float(prof_coords[i, 0]), 6),
            round(float(prof_coords[i, 1]), 6),
            round(float(prof_coords[i, 2]), 6),
        ]
        for i, pid in enumerate(prof_ids)
    }
    field_coord_payload = {
        meta["id"]: {
            "level": meta["level"],
            "name": meta["name"],
            "parent_id": meta.get("parent_id") or "",
            "member_count": meta.get("member_count", 0),
            "keywords": list(meta.get("keywords") or []),
            "vector_source": meta.get("vector_source") or "",
            "position": [
                round(float(field_coords[i, 0]), 6),
                round(float(field_coords[i, 1]), 6),
                round(float(field_coords[i, 2]), 6),
            ],
        }
        for i, meta in enumerate(field_meta)
    }
    course_coord_payload = {
        cid: {
            "instructor_id": course_meta[cid]["instructor_id"],
            "name": course_meta[cid]["name"],
            "position": [
                round(float(course_coords[i, 0]), 6),
                round(float(course_coords[i, 1]), 6),
                round(float(course_coords[i, 2]), 6),
            ],
            "source": "joint_umap_syllabus",
        }
        for i, cid in enumerate(course_ids)
    }

    PROF_COORDS_PATH.write_text(
        json.dumps(prof_coord_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    FIELD_COORDS_PATH.write_text(
        json.dumps(field_coord_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    COURSE_COORDS_PATH.write_text(
        json.dumps(course_coord_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    n_field_only = sum(1 for m in field_meta if m["level"] == "field")
    n_major = sum(1 for m in field_meta if m["level"] == "major")
    n_field_kw = sum(
        1 for m in field_meta if m.get("keywords") and m["level"] == "field"
    )
    print("=== 03_embed_umap_fields ===")
    print(f"method used         : {method_used}")
    print(f"professors in UMAP  : {n_prof}")
    print(f"field nodes         : {n_field_only} (with keywords: {n_field_kw})")
    print(f"major nodes         : {n_major}")
    print(f"courses in UMAP     : {len(course_ids)}")
    print(f"joint points        : {n_prof + n_field + len(course_ids)}")
    print(f"professor coords    : {PROF_COORDS_PATH}")
    print(f"field coords        : {FIELD_COORDS_PATH}")
    print(f"course coords       : {COURSE_COORDS_PATH}")

    if args.update_display:
        update_display(
            prof_ids=prof_ids,
            prof_coords=prof_coords,
            field_meta=field_meta,
            field_coords=field_coords,
            course_ids=course_ids,
            course_coords=course_coords,
            course_meta=course_meta,
            edges=edges,
            analysis_professors=professors,
        )


if __name__ == "__main__":
    main()
