"""
05 — Export display_data.json (UI 表示用)

Sentence Transformers embedding → UMAP 3D → display nodes/edges,
merged with Display metadata (name, affiliation, keywords, description, URLs)
from RIDB + syllabus + analysis_data.

Usage:
  python pipeline/05_export_display_data.py              # full embed + UMAP + meta
  python pipeline/05_export_display_data.py --enrich-only  # keep positions/edges, refresh meta
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity
from umap import UMAP

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
ANALYSIS_PATH = ROOT / "data" / "analysis" / "analysis_data.json"
OUT_PATH = ROOT / "data" / "display" / "display_data.json"

RIDB_FACULTY = RAW / "ridb" / "ridb_yugo_faculty_ja.json"
SYLLABUS_BASIC = RAW / "syllabus" / "all_basic_info.csv"
REMP_FACULTY = RAW / "researchmap" / "remp_yugo_faculty_ja.json"
RESEARCHERS_INDEX = RAW / "researchmap" / "kanazawa_researchers_index.json"

from syllabus_meta import (  # noqa: E402
    load_syllabus_detail_urls,
    load_syllabus_titles,
    split_urls,
    syllabus_url_for_code,
)

MODEL_NAME = "intfloat/multilingual-e5-base"
BATCH_SIZE = 32
RANDOM_STATE = 42
SIMILARITY_TOP_K = 3
SIMILARITY_MIN = 0.55
PREVIEW_LEN = 100

# UMAP layout: local topic clusters (smaller neighborhood) + readable spacing
UMAP_N_NEIGHBORS = 8
UMAP_MIN_DIST = 0.18
UMAP_SPREAD = 1.0
# Soft-pull courses toward their instructor after UMAP (0 = off)
TAUGHT_BY_PULL = 0.4
# Fit coordinates into roughly [-COORD_TARGET, COORD_TARGET]
COORD_TARGET = 28.0

# Public detail page templates
RIDB_DETAIL_TMPL = "https://ridb.kanazawa-u.ac.jp/public/detail.php?id={ridb_id}"

# Hard URL overrides (normalize_name keys) — safety net for known mismatches
RESEARCHMAP_URL_OVERRIDES = {
    "堀優太": "https://researchmap.jp/yuhori",
}

READING_RE = re.compile(r"[（(][^）)]*[）)]")
WHITESPACE_RE = re.compile(r"[ \t\u3000]+")


def load_json(path: Path) -> dict | list:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def display_name(name: str) -> str:
    """Keep kanji name; drop reading in parentheses."""
    name = READING_RE.sub("", name or "")
    name = name.replace("\u3000", " ").replace("　", " ")
    return WHITESPACE_RE.sub(" ", name).strip()


def norm_name(name: str) -> str:
    return re.sub(r"\s+", "", display_name(name))


def remp_display_name(row: dict) -> str:
    if "氏名" in row and row["氏名"]:
        return str(row["氏名"])
    for key, value in row.items():
        if key in {"researchmap_url", "avatar_url", "found"}:
            continue
        if isinstance(value, str) and value.strip() and not value.startswith("http"):
            return value
    return ""


def clean_researchmap_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    # Drop language query for a stable share link
    return re.sub(r"\?lang=[a-zA-Z-]+$", "", url)


def load_researchmap_url_by_name() -> dict[str, str]:
    """norm_name -> researchmap profile URL.

    Preference: hard overrides > researchers index > remp scrape.
    Index wins over remp so a wrong remp scrape cannot override a correct index URL.
    """
    by_name: dict[str, str] = {}
    if REMP_FACULTY.exists():
        for row in load_json(REMP_FACULTY):
            name = norm_name(remp_display_name(row))
            url = clean_researchmap_url(row.get("researchmap_url") or "")
            if name and url and name not in by_name:
                by_name[name] = url
    if RESEARCHERS_INDEX.exists():
        for row in load_json(RESEARCHERS_INDEX):
            name = norm_name(row.get("name") or "")
            url = clean_researchmap_url(row.get("profile_url") or "")
            if name and url:
                by_name[name] = url  # index overrides remp
    for name, url in RESEARCHMAP_URL_OVERRIDES.items():
        by_name[name] = clean_researchmap_url(url)
    return by_name


def load_professor_meta() -> dict[str, dict]:
    """Map prof_{ridb_id} -> {name, affiliation, researchmap_url, ridb_url, url}."""
    ridb = load_json(RIDB_FACULTY)
    remp_urls = load_researchmap_url_by_name()
    meta: dict[str, dict] = {}
    for row in ridb:
        basic = row.get("basic") or {}
        ridb_id = str(basic.get("id") or "").strip()
        if not ridb_id:
            continue
        name = display_name(basic.get("name") or "") or f"prof_{ridb_id}"
        researchmap_url = remp_urls.get(norm_name(basic.get("name") or ""), "")
        ridb_url = RIDB_DETAIL_TMPL.format(ridb_id=ridb_id)
        meta[f"prof_{ridb_id}"] = {
            "name": name,
            "affiliation": (basic.get("affiliation") or "").strip(),
            "researchmap_url": researchmap_url,
            "ridb_url": ridb_url,
            # Prefer researchmap for the primary external link
            "url": researchmap_url or ridb_url,
        }
    return meta


def load_course_meta() -> dict[str, dict]:
    """Map timetable code -> {name, syllabus_url} using scraped 日URL (with lct_idx)."""
    titles = load_syllabus_titles()
    urls = load_syllabus_detail_urls()
    meta: dict[str, dict] = {}
    codes = set(titles) | set(urls)
    # Also cover codes that appear only in all_basic_info
    if SYLLABUS_BASIC.exists():
        with SYLLABUS_BASIC.open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                code = (row.get("時間割番号") or "").strip()
                title = (row.get("科目名") or "").strip()
                if code:
                    codes.add(code)
                    if title and code not in titles:
                        titles[code] = title
    for code in codes:
        syllabus_url = syllabus_url_for_code(code, urls)
        if not syllabus_url and code not in titles:
            continue
        meta[code] = {
            "name": titles.get(code) or "",
            "syllabus_url": syllabus_url,
            "url": syllabus_url,
        }
    return meta


def course_instructor_ids(rec: dict, node: dict | None = None) -> list[str]:
    ids: list[str] = []
    for src in (
        rec.get("instructor_ids"),
        (node or {}).get("instructor_ids"),
        [rec.get("instructor_id")] if rec.get("instructor_id") else None,
        [(node or {}).get("instructor_id")] if (node or {}).get("instructor_id") else None,
    ):
        if not src:
            continue
        for item in src:
            iid = (item or "").strip()
            if iid and iid not in ids:
                ids.append(iid)
    return ids


def course_syllabus_urls(rec: dict, code_meta: dict, codes: list[str]) -> list[str]:
    urls: list[str] = []
    for src in (rec.get("syllabus_urls"), split_urls(rec.get("syllabus_url"))):
        for u in src or []:
            u = (u or "").strip()
            if u and u not in urls:
                urls.append(u)
    for code in codes:
        u = (code_meta.get(code) or {}).get("syllabus_url") or ""
        if u and u not in urls:
            urls.append(u)
    return urls


def timetable_codes_from_course(rec: dict, course_id: str) -> list[str]:
    codes = [c.strip() for c in (rec.get("timetable_codes") or []) if str(c).strip()]
    if codes:
        return codes
    return [timetable_code_from_course_id(course_id)]


def timetable_code_from_course_id(course_id: str) -> str:
    # course_11001.1 / course_11001.1__prof_3055 -> 11001.1
    raw = course_id.removeprefix("course_")
    return raw.split("__", 1)[0]


def embed_source_text(record: dict) -> str | None:
    text = (record.get("text") or "").strip()
    keywords = record.get("keywords") or []
    if text:
        return text
    if keywords:
        return " ".join(str(k).strip() for k in keywords if str(k).strip())
    return None


def analysis_lookup(data: dict) -> dict[str, dict]:
    by_id: dict[str, dict] = {}
    for rec in data.get("professors") or []:
        by_id[rec["id"]] = rec
    for rec in data.get("courses") or []:
        by_id[rec["id"]] = rec
    return by_id


def collect_nodes(data: dict) -> list[dict]:
    nodes: list[dict] = []
    for kind, key in (("professor", "professors"), ("course", "courses")):
        for rec in data.get(key) or []:
            source = embed_source_text(rec)
            if source is None:
                print(f"skip empty: {rec.get('id')}")
                continue
            instructor_ids = course_instructor_ids(rec) if kind == "course" else []
            nodes.append(
                {
                    "id": rec["id"],
                    "type": kind,
                    "source_text": source,
                    "instructor_id": instructor_ids[0] if instructor_ids else rec.get("instructor_id"),
                    "instructor_ids": instructor_ids,
                    "keywords": list(rec.get("keywords") or []),
                    "description": (rec.get("text") or "").strip(),
                }
            )
    return nodes


def enrich_display_node(
    node: dict,
    *,
    prof_meta: dict[str, dict],
    course_meta: dict[str, dict],
    analysis_by_id: dict[str, dict],
) -> dict:
    nid = node["id"]
    ntype = node["type"]
    position = node["position"]
    analysis = analysis_by_id.get(nid) or {}

    if ntype == "professor":
        meta = prof_meta.get(nid) or {}
        researchmap_url = meta.get("researchmap_url") or ""
        ridb_url = meta.get("ridb_url") or ""
        return {
            "id": nid,
            "type": "professor",
            "name": meta.get("name") or nid,
            "affiliation": meta.get("affiliation") or "",
            "researchmap_url": researchmap_url,
            "ridb_url": ridb_url,
            "url": meta.get("url") or researchmap_url or ridb_url,
            "position": position,
            "keywords": list(analysis.get("keywords") or []),
            "description": (analysis.get("text") or "").strip(),
        }

    codes = timetable_codes_from_course(analysis, nid)
    primary_code = codes[0] if codes else timetable_code_from_course_id(nid)
    cmeta = course_meta.get(primary_code) or {}
    instructor_ids = course_instructor_ids(analysis, node)
    instructor_id = instructor_ids[0] if instructor_ids else ""
    instructor_names = [
        (prof_meta.get(iid) or {}).get("name") or iid for iid in instructor_ids
    ]
    instructor_name = " / ".join(n for n in instructor_names if n)
    syllabus_urls = course_syllabus_urls(analysis, course_meta, codes)
    syllabus_url = syllabus_urls[0] if syllabus_urls else (cmeta.get("syllabus_url") or "")
    # Prefer title from any code in the merged group
    name = cmeta.get("name") or ""
    if not name:
        for code in codes:
            name = (course_meta.get(code) or {}).get("name") or ""
            if name:
                break
    return {
        "id": nid,
        "type": "course",
        "name": name or nid,
        "instructor_id": instructor_id,
        "instructor_ids": instructor_ids,
        "instructor_name": instructor_name,
        "timetable_codes": codes,
        "syllabus_url": syllabus_url,
        "syllabus_urls": syllabus_urls,
        "url": syllabus_url,
        "position": position,
        "keywords": list(analysis.get("keywords") or []),
        "description": (analysis.get("text") or "").strip(),
    }


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


def run_umap(embeddings: np.ndarray) -> np.ndarray:
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
    coords = reducer.fit_transform(embeddings)
    return np.asarray(coords, dtype=float)


def soft_align_taught_by(coords: np.ndarray, nodes: list[dict], pull: float = TAUGHT_BY_PULL) -> np.ndarray:
    """Gently pull each course toward its instructor(s) so faculty/course topics co-locate."""
    if pull <= 0:
        return coords
    id_to_i = {n["id"]: i for i, n in enumerate(nodes)}
    out = coords.copy()
    # Courses → mean of instructors
    for i, n in enumerate(nodes):
        if n["type"] != "course":
            continue
        iids = course_instructor_ids(n)
        prof_idxs = [id_to_i[iid] for iid in iids if iid in id_to_i]
        if not prof_idxs:
            continue
        target = coords[prof_idxs].mean(axis=0)
        out[i] = (1.0 - pull) * coords[i] + pull * target
    # Instructors → mean of their (already-pulled) courses
    courses_by_prof: dict[str, list[int]] = {}
    for i, n in enumerate(nodes):
        if n["type"] != "course":
            continue
        for iid in course_instructor_ids(n):
            if iid and iid in id_to_i:
                courses_by_prof.setdefault(iid, []).append(i)
    for iid, idxs in courses_by_prof.items():
        j = id_to_i[iid]
        centroid = out[idxs].mean(axis=0)
        out[j] = (1.0 - pull * 0.6) * coords[j] + (pull * 0.6) * centroid
    return out


def normalize_coords(coords: np.ndarray, target: float = COORD_TARGET) -> np.ndarray:
    """Center and scale so max |component| ≈ target (readable from default camera)."""
    centered = coords - coords.mean(axis=0)
    max_abs = float(np.max(np.abs(centered))) if centered.size else 0.0
    if max_abs < 1e-9:
        return centered
    scaled = centered * (target / max_abs)
    print(
        "coords: "
        f"raw_max_abs={max_abs:.3f} → scaled to ±{target:.1f} "
        f"(range [{scaled.min():.2f}, {scaled.max():.2f}])"
    )
    return scaled.astype(float)


def build_taught_by_edges(nodes: list[dict]) -> list[dict]:
    ids = {n["id"] for n in nodes}
    edges: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for n in nodes:
        if n["type"] != "course":
            continue
        for instructor_id in course_instructor_ids(n):
            if not instructor_id or instructor_id not in ids:
                continue
            key = (instructor_id, n["id"])
            if key in seen:
                continue
            seen.add(key)
            edges.append({"source": instructor_id, "target": n["id"], "type": "taught_by"})
    return edges


def build_similarity_edges(
    node_ids: list[str],
    embeddings: np.ndarray,
    top_k: int = SIMILARITY_TOP_K,
    min_sim: float = SIMILARITY_MIN,
) -> list[dict]:
    if len(node_ids) < 2:
        return []
    sims = cosine_similarity(embeddings)
    np.fill_diagonal(sims, -1.0)

    edges: list[dict] = []
    seen: set[frozenset[str]] = set()
    for i, src in enumerate(node_ids):
        order = np.argsort(-sims[i])[:top_k]
        for j in order:
            score = float(sims[i, j])
            if score < min_sim:
                continue
            dst = node_ids[int(j)]
            pair = frozenset((src, dst))
            if pair in seen:
                continue
            seen.add(pair)
            edges.append(
                {
                    "source": src,
                    "target": dst,
                    "type": "similarity",
                    "score": round(score, 4),
                }
            )
    return edges


def print_sample_nodes(nodes: list[dict]) -> None:
    prof = next((n for n in nodes if n["type"] == "professor"), None)
    course = next((n for n in nodes if n["type"] == "course"), None)
    print("--- sample professor ---")
    if prof:
        sample = {k: (v[:200] + "…") if isinstance(v, str) and len(v) > 200 else v for k, v in prof.items()}
        print(json.dumps(sample, ensure_ascii=True, indent=2))
    print("--- sample course ---")
    if course:
        sample = {k: (v[:200] + "…") if isinstance(v, str) and len(v) > 200 else v for k, v in course.items()}
        print(json.dumps(sample, ensure_ascii=True, indent=2))


def enrich_existing() -> None:
    if not OUT_PATH.exists():
        raise SystemExit(f"Missing existing display data: {OUT_PATH}")

    display = load_json(OUT_PATH)
    analysis = load_json(ANALYSIS_PATH)
    prof_meta = load_professor_meta()
    course_meta = load_course_meta()
    analysis_by_id = analysis_lookup(analysis)

    nodes = [
        enrich_display_node(
            n,
            prof_meta=prof_meta,
            course_meta=course_meta,
            analysis_by_id=analysis_by_id,
        )
        for n in display.get("nodes") or []
    ]
    edges = display.get("edges") or []

    payload = {"nodes": nodes, "edges": edges}
    with OUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    n_prof = sum(1 for n in nodes if n["type"] == "professor")
    n_course = sum(1 for n in nodes if n["type"] == "course")
    named_prof = sum(1 for n in nodes if n["type"] == "professor" and n["name"] != n["id"])
    named_course = sum(1 for n in nodes if n["type"] == "course" and n["name"] != n["id"])
    with_rmap = sum(1 for n in nodes if n["type"] == "professor" and n.get("researchmap_url"))
    with_syll = sum(1 for n in nodes if n["type"] == "course" and n.get("syllabus_url"))

    print("=== 05_export_display_data (--enrich-only) ===")
    print(f"output                    : {OUT_PATH}")
    print(f"nodes (total)             : {len(nodes)}")
    print(f"  professors              : {n_prof} (named {named_prof}, researchmap {with_rmap})")
    print(f"  courses                 : {n_course} (named {named_course}, syllabus {with_syll})")
    print(f"edges (unchanged)         : {len(edges)}")
    print_sample_nodes(nodes)


def run_full() -> None:
    data = load_json(ANALYSIS_PATH)
    nodes_raw = collect_nodes(data)
    if len(nodes_raw) < 3:
        raise SystemExit(f"Too few nodes for UMAP: {len(nodes_raw)}")

    embeddings = encode_texts([n["source_text"] for n in nodes_raw])
    coords = run_umap(embeddings)
    coords = soft_align_taught_by(coords, nodes_raw)
    coords = normalize_coords(coords)

    prof_meta = load_professor_meta()
    course_meta = load_course_meta()
    analysis_by_id = analysis_lookup(data)

    nodes = []
    for i, n in enumerate(nodes_raw):
        draft = {
            "id": n["id"],
            "type": n["type"],
            "position": [
                round(float(coords[i, 0]), 6),
                round(float(coords[i, 1]), 6),
                round(float(coords[i, 2]), 6),
            ],
            "instructor_id": n.get("instructor_id"),
            "instructor_ids": n.get("instructor_ids") or [],
        }
        nodes.append(
            enrich_display_node(
                draft,
                prof_meta=prof_meta,
                course_meta=course_meta,
                analysis_by_id=analysis_by_id,
            )
        )

    taught_by = build_taught_by_edges(nodes_raw)
    similarity = build_similarity_edges(
        [n["id"] for n in nodes_raw],
        embeddings,
    )
    edges = taught_by + similarity

    payload = {"nodes": nodes, "edges": edges}
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    n_prof = sum(1 for n in nodes if n["type"] == "professor")
    n_course = sum(1 for n in nodes if n["type"] == "course")
    print("=== 05_export_display_data ===")
    print(f"model                     : {MODEL_NAME}")
    print(f"output                    : {OUT_PATH}")
    print(f"nodes (total)             : {len(nodes)}")
    print(f"  professors              : {n_prof}")
    print(f"  courses                 : {n_course}")
    print(f"edges (total)             : {len(edges)}")
    print(f"  taught_by               : {len(taught_by)}")
    print(f"  similarity              : {len(similarity)}")
    print_sample_nodes(nodes)


def main() -> None:
    parser = argparse.ArgumentParser(description="Embed + UMAP + display metadata")
    parser.add_argument(
        "--enrich-only",
        action="store_true",
        help="Reuse existing positions/edges; only merge display metadata",
    )
    args = parser.parse_args()
    if args.enrich_only:
        enrich_existing()
    else:
        run_full()


if __name__ == "__main__":
    main()
