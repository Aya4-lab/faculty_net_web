"""
STEP 4: Sentence Transformers embedding → UMAP 3D → display_data.json

Input:  data/analysis/analysis_data.json
Output: data/display/display_data.json
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity
from umap import UMAP

ROOT = Path(__file__).resolve().parent.parent
ANALYSIS_PATH = ROOT / "data" / "analysis" / "analysis_data.json"
OUT_PATH = ROOT / "data" / "display" / "display_data.json"

# Multilingual model (JA/EN mixed text)
MODEL_NAME = "intfloat/multilingual-e5-base"
BATCH_SIZE = 32
RANDOM_STATE = 42
SIMILARITY_TOP_K = 3
SIMILARITY_MIN = 0.55  # ignore weak pairs
PREVIEW_LEN = 100


def load_analysis(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def embed_source_text(record: dict) -> str | None:
    """
    Prefer real prose. If text is empty but keywords exist, join keywords
    for embedding only (does not write back into analysis data).
    Skip fully empty records.
    """
    text = (record.get("text") or "").strip()
    keywords = record.get("keywords") or []
    if text:
        return text
    if keywords:
        return " ".join(str(k).strip() for k in keywords if str(k).strip())
    return None


def collect_nodes(data: dict) -> list[dict]:
    nodes: list[dict] = []
    for kind, key in (("professor", "professors"), ("course", "courses")):
        for rec in data.get(key) or []:
            source = embed_source_text(rec)
            if source is None:
                print(f"skip empty: {rec.get('id')}")
                continue
            nodes.append(
                {
                    "id": rec["id"],
                    "type": kind,
                    "source_text": source,
                    "instructor_id": rec.get("instructor_id"),
                }
            )
    return nodes


def encode_texts(texts: list[str]) -> np.ndarray:
    from sentence_transformers import SentenceTransformer

    print(f"Loading model: {MODEL_NAME}")
    model = SentenceTransformer(MODEL_NAME)
    # E5 convention: document / passage prefix
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
    n_neighbors = min(15, max(2, n - 1))
    print(f"UMAP: n={n}, n_neighbors={n_neighbors}, n_components=3, metric=cosine")
    reducer = UMAP(
        n_components=3,
        n_neighbors=n_neighbors,
        metric="cosine",
        random_state=RANDOM_STATE,
        min_dist=0.1,
    )
    coords = reducer.fit_transform(embeddings)
    # Center for Three.js-friendly origin
    coords = coords - coords.mean(axis=0)
    return coords.astype(float)


def text_preview(text: str, limit: int = PREVIEW_LEN) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "..."


def build_taught_by_edges(nodes: list[dict]) -> list[dict]:
    ids = {n["id"] for n in nodes}
    edges: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for n in nodes:
        if n["type"] != "course":
            continue
        instructor_id = n.get("instructor_id")
        if not instructor_id or instructor_id not in ids:
            continue
        key = (instructor_id, n["id"])
        if key in seen:
            continue
        seen.add(key)
        edges.append(
            {
                "source": instructor_id,
                "target": n["id"],
                "type": "taught_by",
            }
        )
    return edges


def build_similarity_edges(
    node_ids: list[str],
    embeddings: np.ndarray,
    top_k: int = SIMILARITY_TOP_K,
    min_sim: float = SIMILARITY_MIN,
) -> list[dict]:
    """Add undirected similarity edges for top-k neighbors above threshold."""
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


def main() -> None:
    data = load_analysis(ANALYSIS_PATH)
    nodes_raw = collect_nodes(data)
    if len(nodes_raw) < 3:
        raise SystemExit(f"Too few nodes for UMAP: {len(nodes_raw)}")

    embeddings = encode_texts([n["source_text"] for n in nodes_raw])
    coords = run_umap(embeddings)

    nodes = []
    for i, n in enumerate(nodes_raw):
        nodes.append(
            {
                "id": n["id"],
                "type": n["type"],
                "position": [
                    round(float(coords[i, 0]), 6),
                    round(float(coords[i, 1]), 6),
                    round(float(coords[i, 2]), 6),
                ],
                "text_preview": text_preview(n["source_text"]),
            }
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
    print("=== 02_analyze_umap ===")
    print(f"model                     : {MODEL_NAME}")
    print(f"output                    : {OUT_PATH}")
    print(f"nodes (total)             : {len(nodes)}")
    print(f"  professors              : {n_prof}")
    print(f"  courses                 : {n_course}")
    print(f"edges (total)             : {len(edges)}")
    print(f"  taught_by               : {len(taught_by)}")
    print(f"  similarity              : {len(similarity)}")


if __name__ == "__main__":
    main()
