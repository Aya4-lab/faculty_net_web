"""
Faculty Network 3D Explorer API

Serves display_data.json merged with Analysis-layer field landmarks:
  - field_coords.json / professor_coords_with_fields.json
  - field_nodes.csv, edges_professor_field.csv, edges_professor_course.csv
  - RIDB photo_url for professor billboards

Usage (from project_root/):
  python app_v2.py
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

from flask import Flask, jsonify, send_from_directory

APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent

DISPLAY_JSON = PROJECT_ROOT / "data" / "display" / "display_data.json"
if not DISPLAY_JSON.exists():
    DISPLAY_JSON = APP_DIR / "display_data.json"

ANALYSIS = PROJECT_ROOT / "data" / "analysis"
RAW_RIDB = PROJECT_ROOT / "data" / "raw" / "ridb"

FIELD_COORDS_PATH = ANALYSIS / "field_coords.json"
PROF_COORDS_PATH = ANALYSIS / "professor_coords_with_fields.json"
COURSE_COORDS_PATH = ANALYSIS / "course_coords.json"
COURSE_NODES_CSV = ANALYSIS / "course_nodes.csv"
EDGES_PF_CSV = ANALYSIS / "edges_professor_field.csv"
EDGES_PC_CSV = ANALYSIS / "edges_professor_course.csv"
FIELD_NODES_CSV = ANALYSIS / "field_nodes.csv"
RIDB_FACULTY = RAW_RIDB / "ridb_yugo_faculty_ja.json"

app = Flask(__name__, static_folder="web", static_url_path="/web")
app.config["JSON_AS_ASCII"] = False


def _position_to_dict(pos) -> dict:
    if isinstance(pos, list) and len(pos) >= 3:
        return {"x": float(pos[0]), "y": float(pos[1]), "z": float(pos[2])}
    if isinstance(pos, dict):
        return {
            "x": float(pos.get("x", 0)),
            "y": float(pos.get("y", 0)),
            "z": float(pos.get("z", 0)),
        }
    return {"x": 0.0, "y": 0.0, "z": 0.0}


def _text_preview(node: dict, max_len: int = 480) -> str:
    raw = (node.get("text_preview") or node.get("description") or "").strip()
    if len(raw) <= max_len:
        return raw
    return raw[: max_len - 1].rstrip() + "…"


def _first_url(*candidates: str) -> str:
    for value in candidates:
        if isinstance(value, list):
            for item in value:
                url = _first_url(item)
                if url:
                    return url
            continue
        # Allow pipe-joined multi-URL fields
        text = (value or "").strip()
        if "|" in text:
            for part in text.split("|"):
                url = _first_url(part)
                if url:
                    return url
            continue
        url = text
        if url.startswith("http://") or url.startswith("https://"):
            return url
    return ""


def _split_pipe_list(raw) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    text = str(raw).strip()
    if not text:
        return []
    if "|" in text:
        return [p.strip() for p in text.split("|") if p.strip()]
    return [text]


def _collect_urls(*candidates) -> list[str]:
    out: list[str] = []
    for value in candidates:
        for item in _split_pipe_list(value):
            if item.startswith("http://") or item.startswith("https://"):
                if item not in out:
                    out.append(item)
    return out


def _load_json(path: Path):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _load_photo_by_prof_id() -> dict[str, str]:
    """prof_{ridb_id} -> photo_url (skip placeholder noimg)."""
    ridb = _load_json(RIDB_FACULTY) or []
    out: dict[str, str] = {}
    for row in ridb:
        basic = row.get("basic") or {}
        ridb_id = str(basic.get("id") or "").strip()
        photo = (basic.get("photo_url") or "").strip()
        if not ridb_id or not photo:
            continue
        if "noimg" in photo.lower() or "human_noimg" in photo.lower():
            continue
        out[f"prof_{ridb_id}"] = photo
    return out


def _load_field_nodes_meta() -> dict[str, dict]:
    """id -> csv row for active field/major nodes."""
    if not FIELD_NODES_CSV.exists():
        return {}
    meta: dict[str, dict] = {}
    with FIELD_NODES_CSV.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            nid = (row.get("id") or "").strip()
            if not nid:
                continue
            meta[nid] = row
    return meta


def _load_belongs_to_field_edges() -> list[dict]:
    if not EDGES_PF_CSV.exists():
        return []
    edges: list[dict] = []
    with EDGES_PF_CSV.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            src = (row.get("source") or "").strip()
            tgt = (row.get("target") or "").strip()
            if not src or not tgt:
                continue
            edges.append(
                {
                    "source": src,
                    "target": tgt,
                    "type": "belongs_to_field",
                    "method": (row.get("method") or "").strip(),
                    "similarity": 0.0,
                    "score": 0.0,
                }
            )
    return edges


def _load_course_nodes_meta() -> dict[str, dict]:
    """course_id -> display row from analysis/course_nodes.csv."""
    if not COURSE_NODES_CSV.exists():
        return {}
    meta: dict[str, dict] = {}
    with COURSE_NODES_CSV.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            cid = (row.get("course_id") or "").strip()
            if cid:
                meta[cid] = row
    return meta


def _load_taught_by_edges() -> list[dict]:
    if not EDGES_PC_CSV.exists():
        return []
    edges: list[dict] = []
    with EDGES_PC_CSV.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            src = (row.get("professor_id") or "").strip()
            tgt = (row.get("course_id") or "").strip()
            if not src or not tgt:
                continue
            edges.append(
                {
                    "source": src,
                    "target": tgt,
                    "type": "taught_by",
                    "similarity": 0.0,
                    "score": 0.0,
                }
            )
    return edges


def _build_field_nodes() -> list[dict]:
    coords = _load_json(FIELD_COORDS_PATH) or {}
    meta = _load_field_nodes_meta()
    nodes: list[dict] = []
    for fid, info in coords.items():
        level = info.get("level") or "field"
        csv_row = meta.get(fid) or {}
        ntype = "major" if level == "major" else "field"
        kw = list(info.get("keywords") or [])
        if not kw:
            raw_kw = (csv_row.get("keywords") or "").strip()
            if raw_kw:
                kw = [p.strip() for p in raw_kw.split("|") if p.strip()]
        nodes.append(
            {
                "id": fid,
                "type": ntype,
                "name": info.get("name") or csv_row.get("name") or fid,
                "parent_id": info.get("parent_id") or csv_row.get("parent_id") or "",
                "major_name": csv_row.get("major_name") or "",
                "member_count": int(
                    info.get("member_count")
                    or csv_row.get("member_count")
                    or 0
                ),
                "url": (csv_row.get("url") or "").strip(),
                "keywords": kw,
                "text_preview": "",
                "description": "",
                "position": _position_to_dict(info.get("position")),
            }
        )
    return nodes


def _hierarchy_edges(field_nodes: list[dict]) -> list[dict]:
    field_ids = {n["id"] for n in field_nodes}
    edges: list[dict] = []
    for node in field_nodes:
        if node["type"] != "field":
            continue
        parent = node.get("parent_id") or ""
        if parent and parent in field_ids:
            edges.append(
                {
                    "source": node["id"],
                    "target": parent,
                    "type": "field_hierarchy",
                    "similarity": 0.0,
                    "score": 0.0,
                }
            )
    return edges


def load_network_data(json_path: Path) -> dict:
    if not json_path.exists():
        return {"nodes": [], "edges": []}

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    raw_nodes = payload.get("nodes", [])
    photos = _load_photo_by_prof_id()
    prof_coords = _load_json(PROF_COORDS_PATH) or {}
    course_coords = _load_json(COURSE_COORDS_PATH) or {}
    course_meta = _load_course_nodes_meta()

    # First pass: professors (needed to resolve course instructor_name)
    prof_name_by_id: dict[str, str] = {}
    prof_pos_by_id: dict[str, dict] = {}
    for node in raw_nodes:
        if node.get("type") != "professor":
            continue
        nid = node.get("id", "")
        name = (node.get("name") or "").strip()
        if nid and name and name != nid:
            prof_name_by_id[nid] = name
        if nid in prof_coords:
            prof_pos_by_id[nid] = _position_to_dict(prof_coords[nid])
        else:
            prof_pos_by_id[nid] = _position_to_dict(node.get("position", [0, 0, 0]))

    normalized_nodes: list[dict] = []
    seen_course_ids: set[str] = set()
    for node in raw_nodes:
        ntype = node.get("type", "unknown")
        nid = node.get("id", "")
        name = (node.get("name") or "").strip() or nid
        keywords = list(node.get("keywords") or [])
        subfield_keywords = list(node.get("subfield_keywords") or [])
        preview = _text_preview(node)

        if ntype == "professor":
            researchmap_url = _first_url(node.get("researchmap_url"), node.get("url"))
            ridb_url = _first_url(node.get("ridb_url"))
            primary = _first_url(researchmap_url, ridb_url, node.get("url"))
            normalized_nodes.append(
                {
                    "id": nid,
                    "type": "professor",
                    "name": name,
                    "affiliation": (node.get("affiliation") or "").strip(),
                    "keywords": keywords,
                    "subfield_keywords": subfield_keywords,
                    "text_preview": preview,
                    "description": (node.get("description") or "").strip(),
                    "researchmap_url": researchmap_url,
                    "ridb_url": ridb_url,
                    "url": primary,
                    "photo_url": photos.get(nid, ""),
                    "position": prof_pos_by_id.get(
                        nid, _position_to_dict(node.get("position", [0, 0, 0]))
                    ),
                }
            )
        elif ntype == "course":
            cmeta = course_meta.get(nid) or {}
            instructor_ids = _split_pipe_list(
                node.get("instructor_ids")
            ) or _split_pipe_list(cmeta.get("instructor_ids"))
            instructor_id = (node.get("instructor_id") or "").strip()
            if not instructor_id:
                instructor_id = (cmeta.get("instructor_id") or "").strip()
            if instructor_id and instructor_id not in instructor_ids:
                instructor_ids.insert(0, instructor_id)
            elif not instructor_id and instructor_ids:
                instructor_id = instructor_ids[0]
            instructor_names = [
                prof_name_by_id.get(iid, "") or iid for iid in instructor_ids
            ]
            instructor_name = (
                (node.get("instructor_name") or "").strip()
                or " / ".join(n for n in instructor_names if n)
            )
            syllabus_urls = _collect_urls(
                cmeta.get("syllabus_urls"),
                cmeta.get("syllabus_url"),
                node.get("syllabus_urls"),
                node.get("syllabus_url"),
                node.get("url"),
            )
            syllabus_url = syllabus_urls[0] if syllabus_urls else ""
            name = (cmeta.get("course_name") or name).strip() or nid
            if nid in course_coords:
                position = _position_to_dict(course_coords[nid].get("position"))
            elif instructor_id and instructor_id in prof_pos_by_id:
                base = prof_pos_by_id[instructor_id]
                h = sum(ord(c) for c in nid) % 360
                rad = math.radians(h)
                position = {
                    "x": base["x"] + 1.6 * math.cos(rad),
                    "y": base["y"] + 0.4 * math.sin(rad * 1.7),
                    "z": base["z"] + 1.6 * math.sin(rad),
                }
            else:
                position = _position_to_dict(node.get("position", [0, 0, 0]))
            seen_course_ids.add(nid)
            normalized_nodes.append(
                {
                    "id": nid,
                    "type": "course",
                    "name": name,
                    "instructor_id": instructor_id,
                    "instructor_ids": instructor_ids,
                    "instructor_name": instructor_name,
                    "keywords": keywords,
                    "text_preview": preview,
                    "description": (node.get("description") or "").strip(),
                    "syllabus_url": syllabus_url,
                    "syllabus_urls": syllabus_urls,
                    "url": syllabus_url,
                    "position": position,
                }
            )
        elif ntype in {"field", "major"}:
            # Field/major nodes come solely from field_coords via _build_field_nodes().
            continue
        else:
            normalized_nodes.append(
                {
                    "id": nid,
                    "type": ntype,
                    "name": name,
                    "keywords": keywords,
                    "text_preview": preview,
                    "url": _first_url(node.get("url")),
                    "position": _position_to_dict(node.get("position", [0, 0, 0])),
                }
            )

    # Courses present only in analysis course_nodes / course_coords
    for cid, crow in course_meta.items():
        if cid in seen_course_ids:
            continue
        instructor_ids = _split_pipe_list(crow.get("instructor_ids"))
        instructor_id = (crow.get("instructor_id") or "").strip()
        if instructor_id and instructor_id not in instructor_ids:
            instructor_ids.insert(0, instructor_id)
        elif not instructor_id and instructor_ids:
            instructor_id = instructor_ids[0]
        if cid not in course_coords and instructor_id not in prof_pos_by_id:
            continue
        if cid in course_coords:
            position = _position_to_dict(course_coords[cid].get("position"))
        else:
            base = prof_pos_by_id[instructor_id]
            position = {"x": base["x"], "y": base["y"], "z": base["z"]}
        syllabus_urls = _collect_urls(crow.get("syllabus_urls"), crow.get("syllabus_url"))
        syllabus_url = syllabus_urls[0] if syllabus_urls else ""
        instructor_name = " / ".join(
            prof_name_by_id.get(iid, "") or iid for iid in instructor_ids
        )
        normalized_nodes.append(
            {
                "id": cid,
                "type": "course",
                "name": (crow.get("course_name") or cid).strip(),
                "instructor_id": instructor_id,
                "instructor_ids": instructor_ids,
                "instructor_name": instructor_name,
                "keywords": [],
                "text_preview": "",
                "description": "",
                "syllabus_url": syllabus_url,
                "syllabus_urls": syllabus_urls,
                "url": syllabus_url,
                "position": position,
            }
        )

    field_nodes = _build_field_nodes()
    normalized_nodes.extend(field_nodes)

    normalized_edges: list[dict] = []
    for edge in payload.get("edges", []):
        raw_score = edge.get("similarity", edge.get("score", 0.0))
        try:
            score = float(raw_score or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        etype = edge.get("type", "similarity")
        if etype in {"belongs_to_field", "field_hierarchy", "taught_by"}:
            continue  # replaced from analysis layer when available
        normalized_edges.append(
            {
                "source": edge.get("source", edge.get("source_id", "")),
                "target": edge.get("target", edge.get("target_id", "")),
                "type": etype,
                "similarity": score,
                "score": score,
            }
        )

    node_ids = {n["id"] for n in normalized_nodes}
    for edge in _load_belongs_to_field_edges():
        if edge["source"] in node_ids and edge["target"] in node_ids:
            normalized_edges.append(edge)
    for edge in _hierarchy_edges(field_nodes):
        normalized_edges.append(edge)

    taught = _load_taught_by_edges()
    if taught:
        for edge in taught:
            if edge["source"] in node_ids and edge["target"] in node_ids:
                normalized_edges.append(edge)
    else:
        # Fallback: keep taught_by from display_data
        for edge in payload.get("edges", []):
            if edge.get("type") != "taught_by":
                continue
            src = edge.get("source", edge.get("source_id", ""))
            tgt = edge.get("target", edge.get("target_id", ""))
            if src in node_ids and tgt in node_ids:
                normalized_edges.append(
                    {
                        "source": src,
                        "target": tgt,
                        "type": "taught_by",
                        "similarity": 0.0,
                        "score": 0.0,
                    }
                )

    return {"nodes": normalized_nodes, "edges": normalized_edges}


DATA = load_network_data(DISPLAY_JSON)


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/network")
def api_network():
    return jsonify(DATA)


@app.get("/api/reload")
def api_reload():
    """Dev helper: rebuild merged payload without restarting Flask."""
    global DATA
    DATA = load_network_data(DISPLAY_JSON)
    n_field = sum(1 for n in DATA["nodes"] if n["type"] in {"field", "major"})
    n_prof = sum(1 for n in DATA["nodes"] if n["type"] == "professor")
    return jsonify(
        {
            "ok": True,
            "nodes": len(DATA["nodes"]),
            "professors": n_prof,
            "fields": n_field,
            "edges": len(DATA["edges"]),
        }
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=True)
