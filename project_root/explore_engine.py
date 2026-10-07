"""
Free-text exploration engine (Analysis layer).

Loads precomputed L2-normalized node embeddings from data/analysis/*.npz
and ranks nodes against a query encoded with multilingual-e5-base
(query: … / passage: … convention).
"""

from __future__ import annotations

import csv
import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

MODEL_NAME = "intfloat/multilingual-e5-base"

_TOKEN_SPLIT = re.compile(r"[\s、,/．。・：:；;（）()「」『』【】\[\]{}|]+")


@dataclass
class EmbeddingBank:
    ids: list[str]
    types: list[str]
    matrix: np.ndarray  # (N, D) float32, L2-normalized
    keywords: dict[str, list[str]]
    names: dict[str, str]
    positions: dict[str, dict[str, float]]


_bank: EmbeddingBank | None = None
_model = None
_lock = threading.Lock()
_load_error: str | None = None


def _clean_keywords(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        out = []
        for item in raw:
            t = str(item or "").strip()
            if t:
                out.append(t)
        return out
    text = str(raw).strip()
    if not text:
        return []
    if "|" in text:
        return [p.strip() for p in text.split("|") if p.strip()]
    if "," in text:
        return [p.strip() for p in text.split(",") if p.strip()]
    return [text]


def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _position_dict(pos: Any) -> dict[str, float] | None:
    if isinstance(pos, list) and len(pos) >= 3:
        return {"x": float(pos[0]), "y": float(pos[1]), "z": float(pos[2])}
    if isinstance(pos, dict):
        return {
            "x": float(pos.get("x", 0)),
            "y": float(pos.get("y", 0)),
            "z": float(pos.get("z", 0)),
        }
    return None


def build_bank(analysis_dir: Path) -> EmbeddingBank:
    """Merge professor / field / course embedding matrices + metadata."""
    analysis_dir = Path(analysis_dir)
    keyword_map: dict[str, list[str]] = {}
    name_map: dict[str, str] = {}
    pos_map: dict[str, dict[str, float]] = {}

    analysis = _load_json(analysis_dir / "analysis_data.json") or {}
    for row in analysis.get("professors") or []:
        pid = (row.get("id") or "").strip()
        if not pid:
            continue
        kws = _clean_keywords(row.get("keywords"))
        sub = _clean_keywords(row.get("subfield_keywords"))
        merged = list(dict.fromkeys([*kws, *sub]))
        keyword_map[pid] = merged
        name_map[pid] = pid

    for row in analysis.get("courses") or []:
        cid = (row.get("id") or "").strip()
        if not cid:
            continue
        keyword_map[cid] = _clean_keywords(row.get("keywords"))
        name_map[cid] = cid

    field_csv = analysis_dir / "field_nodes.csv"
    if field_csv.exists():
        with field_csv.open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                fid = (row.get("id") or "").strip()
                if not fid:
                    continue
                keyword_map[fid] = _clean_keywords(row.get("keywords"))
                name_map[fid] = (row.get("name") or fid).strip()

    for path, default_type in (
        (analysis_dir / "professor_coords_with_fields.json", "professor"),
        (analysis_dir / "course_coords.json", "course"),
        (analysis_dir / "field_coords.json", "field"),
    ):
        payload = _load_json(path) or {}
        for nid, info in payload.items():
            if isinstance(info, dict):
                name = (info.get("name") or name_map.get(nid) or nid).strip()
                name_map[nid] = name
                if info.get("keywords"):
                    keyword_map[nid] = _clean_keywords(info.get("keywords"))
                pos = _position_dict(info.get("position") or info.get("coords") or info)
                if pos:
                    pos_map[nid] = pos
            elif isinstance(info, list):
                pos = _position_dict(info)
                if pos:
                    pos_map[nid] = pos

    ids: list[str] = []
    types: list[str] = []
    blocks: list[np.ndarray] = []

    specs = (
        ("professor_embeddings.npz", "professor"),
        ("field_embeddings.npz", "field"),
        ("course_embeddings.npz", "course"),
    )
    for fname, ntype in specs:
        path = analysis_dir / fname
        if not path.exists():
            continue
        data = np.load(path, allow_pickle=True)
        row_ids = [str(x) for x in data["ids"].tolist()]
        vecs = np.asarray(data["vectors"], dtype=np.float32)
        # Ensure L2-normalized
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms = np.maximum(norms, 1e-12)
        vecs = vecs / norms
        levels = data["levels"].tolist() if "levels" in data.files else None
        for i, nid in enumerate(row_ids):
            ids.append(nid)
            if levels is not None:
                level = str(levels[i] or ntype)
                types.append("major" if level == "major" else "field")
            else:
                types.append(ntype)
        blocks.append(vecs)

    if not blocks:
        raise FileNotFoundError(
            f"No embedding .npz files found under {analysis_dir}"
        )

    matrix = np.vstack(blocks).astype(np.float32)
    return EmbeddingBank(
        ids=ids,
        types=types,
        matrix=matrix,
        keywords=keyword_map,
        names=name_map,
        positions=pos_map,
    )


def ensure_bank(analysis_dir: Path) -> EmbeddingBank:
    global _bank, _load_error
    if _bank is not None:
        return _bank
    with _lock:
        if _bank is not None:
            return _bank
        try:
            _bank = build_bank(analysis_dir)
            _load_error = None
        except Exception as exc:  # noqa: BLE001 — surface to API
            _load_error = str(exc)
            raise
        return _bank


def ensure_model():
    global _model, _load_error
    if _model is not None:
        return _model
    with _lock:
        if _model is not None:
            return _model
        try:
            from sentence_transformers import SentenceTransformer

            _model = SentenceTransformer(MODEL_NAME)
            _load_error = None
        except Exception as exc:  # noqa: BLE001
            _load_error = str(exc)
            raise
        return _model


def encode_query(text: str) -> np.ndarray:
    model = ensure_model()
    vec = model.encode(
        [f"query: {text}"],
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return np.asarray(vec[0], dtype=np.float32)


def _query_tokens(text: str) -> list[str]:
    parts = [p.strip().lower() for p in _TOKEN_SPLIT.split(text) if p.strip()]
    # Keep tokens of length >= 2 (JP/EN)
    return [p for p in parts if len(p) >= 2]


def compute_reasons(
    query: str,
    *,
    node_type: str,
    keywords: list[str],
    score: float,
) -> list[str]:
    reasons: list[str] = []
    q_lower = query.lower()
    tokens = _query_tokens(query)

    overlap: list[str] = []
    for kw in keywords:
        k = (kw or "").strip()
        if not k:
            continue
        kl = k.lower()
        if kl in q_lower or any(t in kl or kl in t for t in tokens if len(t) >= 2):
            overlap.append(k)
        elif len(k) >= 2 and k in query:
            overlap.append(k)

    # de-dupe preserving order
    seen = set()
    uniq = []
    for k in overlap:
        if k not in seen:
            seen.add(k)
            uniq.append(k)

    if uniq:
        reasons.append("共通キーワード: " + "、".join(uniq[:3]))

    if score >= 0.86:
        reasons.append("高い意味的類似")
    elif score >= 0.78:
        reasons.append("意味的に近い")

    type_hint = {
        "field": "研究分野の近接",
        "major": "大分野の近接",
        "professor": "研究内容の類似",
        "course": "講義内容の類似",
    }.get(node_type, "意味的なつながり")
    if type_hint not in reasons:
        reasons.append(type_hint)

    return reasons[:3]


def explore_text(
    text: str,
    analysis_dir: Path,
    *,
    top_k: int = 24,
    min_score: float = 0.55,
    types: list[str] | None = None,
) -> dict[str, Any]:
    """
    Rank nodes by cosine similarity to the free-text query.

    Returns centroid position (UMAP space), hits, and dynamic user edges.
    Course (講義) embeddings are scored independently of their instructor;
    a diversity backfill keeps enough lecture hits when professors dominate.
    """
    clean = (text or "").strip()
    if len(clean) < 4:
        raise ValueError("文章が短すぎます（4文字以上）")

    bank = ensure_bank(analysis_dir)
    q = encode_query(clean)
    scores = bank.matrix @ q  # (N,)

    allow = None
    if types:
        allow = {t.lower() for t in types}
        # majors share field bank
        if "field" in allow:
            allow.add("major")

    ranked: list[tuple[int, float]] = []
    for i, s in enumerate(scores.tolist()):
        if s < min_score:
            continue
        ntype = bank.types[i]
        if allow and ntype not in allow:
            continue
        ranked.append((i, float(s)))
    ranked.sort(key=lambda x: x[1], reverse=True)

    k = max(1, int(top_k))
    selected = ranked[:k]

    # Ensure lecture nodes are not crowded out by professors/fields.
    # Courses use their own embedding rows (not instructor vectors).
    want_courses = allow is None or "course" in allow
    if want_courses:
        min_course_hits = min(6, k)
        course_soft = max(0.45, float(min_score) - 0.07)
        selected_idx = {i for i, _ in selected}
        courses_picked = [(i, s) for i, s in selected if bank.types[i] == "course"]
        others_picked = [(i, s) for i, s in selected if bank.types[i] != "course"]
        if len(courses_picked) < min_course_hits:
            extras: list[tuple[int, float]] = []
            for i, s in ranked:
                if i in selected_idx or bank.types[i] != "course":
                    continue
                if s < course_soft:
                    continue
                extras.append((i, s))
                if len(courses_picked) + len(extras) >= min_course_hits:
                    break
            if extras:
                all_courses = courses_picked + extras
                all_courses.sort(key=lambda x: x[1], reverse=True)
                others_picked.sort(key=lambda x: x[1], reverse=True)
                room = max(0, k - len(all_courses))
                selected = all_courses + others_picked[:room]
                selected.sort(key=lambda x: x[1], reverse=True)

    ranked = selected

    hits: list[dict[str, Any]] = []
    weight_sum = 0.0
    cx = cy = cz = 0.0

    for i, score in ranked:
        nid = bank.ids[i]
        ntype = bank.types[i]
        kws = bank.keywords.get(nid) or []
        reasons = compute_reasons(
            clean, node_type=ntype, keywords=kws, score=score
        )
        pos = bank.positions.get(nid)
        hit = {
            "id": nid,
            "type": ntype,
            "name": bank.names.get(nid) or nid,
            "score": round(score, 4),
            "reasons": reasons,
            "keywords": kws[:12],
            "position": pos,
        }
        hits.append(hit)
        if pos:
            w = max(score, 1e-3) ** 2
            cx += pos["x"] * w
            cy += pos["y"] * w
            cz += pos["z"] * w
            weight_sum += w

    if weight_sum > 0:
        centroid = {
            "x": cx / weight_sum,
            "y": cy / weight_sum,
            "z": cz / weight_sum,
        }
    elif hits and hits[0].get("position"):
        centroid = dict(hits[0]["position"])
    else:
        centroid = {"x": 0.0, "y": 0.0, "z": 0.0}

    user_edges = [
        {
            "source": "user_interest",
            "target": h["id"],
            "type": "user_affinity",
            "score": h["score"],
            "similarity": h["score"],
            "reasons": h["reasons"],
        }
        for h in hits
    ]

    return {
        "ok": True,
        "query": clean,
        "model": MODEL_NAME,
        "centroid": centroid,
        "hits": hits,
        "user_edges": user_edges,
        "count": len(hits),
    }


def status(analysis_dir: Path) -> dict[str, Any]:
    info: dict[str, Any] = {
        "model": MODEL_NAME,
        "model_loaded": _model is not None,
        "bank_loaded": _bank is not None,
        "error": _load_error,
    }
    try:
        bank = ensure_bank(analysis_dir)
        info["nodes"] = len(bank.ids)
        info["dim"] = int(bank.matrix.shape[1]) if bank.matrix.ndim == 2 else 0
    except Exception as exc:  # noqa: BLE001
        info["error"] = str(exc)
    return info
