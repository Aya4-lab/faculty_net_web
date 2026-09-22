import json
from pathlib import Path
from flask import Flask, jsonify, send_from_directory

APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent

# 今回新しく生成した display_data.json を指定
DISPLAY_JSON = PROJECT_ROOT / "data" / "display" / "display_data.json"
if not DISPLAY_JSON.exists():
    DISPLAY_JSON = APP_DIR / "display_data.json"

app = Flask(__name__, static_folder="web", static_url_path="/web")
app.config["JSON_AS_ASCII"] = False


def _position_to_dict(pos):
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


def load_network_data(json_path: Path):
    if not json_path.exists():
        return {"nodes": [], "edges": []}

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    raw_nodes = payload.get("nodes", [])

    # First pass: professors (needed to resolve course instructor_name)
    prof_name_by_id: dict[str, str] = {}
    for node in raw_nodes:
        if node.get("type") != "professor":
            continue
        nid = node.get("id", "")
        name = (node.get("name") or "").strip()
        if nid and name and name != nid:
            prof_name_by_id[nid] = name

    normalized_nodes = []
    for node in raw_nodes:
        ntype = node.get("type", "unknown")
        nid = node.get("id", "")
        name = (node.get("name") or "").strip() or nid
        keywords = list(node.get("keywords") or [])
        preview = _text_preview(node)

        if ntype == "professor":
            normalized_nodes.append(
                {
                    "id": nid,
                    "type": "professor",
                    "name": name,
                    "affiliation": (node.get("affiliation") or "").strip(),
                    "keywords": keywords,
                    "text_preview": preview,
                    "description": (node.get("description") or "").strip(),
                    "position": _position_to_dict(node.get("position", [0, 0, 0])),
                }
            )
        elif ntype == "course":
            instructor_id = (node.get("instructor_id") or "").strip()
            instructor_name = (
                (node.get("instructor_name") or "").strip()
                or prof_name_by_id.get(instructor_id, "")
            )
            normalized_nodes.append(
                {
                    "id": nid,
                    "type": "course",
                    "name": name,
                    "instructor_id": instructor_id,
                    "instructor_name": instructor_name,
                    "keywords": keywords,
                    "text_preview": preview,
                    "description": (node.get("description") or "").strip(),
                    "position": _position_to_dict(node.get("position", [0, 0, 0])),
                }
            )
        else:
            normalized_nodes.append(
                {
                    "id": nid,
                    "type": ntype,
                    "name": name,
                    "keywords": keywords,
                    "text_preview": preview,
                    "position": _position_to_dict(node.get("position", [0, 0, 0])),
                }
            )

    normalized_edges = []
    for edge in payload.get("edges", []):
        raw_score = edge.get("similarity", edge.get("score", 0.0))
        try:
            score = float(raw_score or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        normalized_edges.append(
            {
                "source": edge.get("source", edge.get("source_id", "")),
                "target": edge.get("target", edge.get("target_id", "")),
                "type": edge.get("type", "similarity"),
                "similarity": score,
                "score": score,
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


if __name__ == "__main__":
    # ポートを 5001 などに変えておけば旧プロトタイプと並行起動も可能
    app.run(host="127.0.0.1", port=5001, debug=True)
