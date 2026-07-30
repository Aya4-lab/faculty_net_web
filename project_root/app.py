import json
from pathlib import Path
from typing import Dict

from flask import Flask, jsonify, send_from_directory

APP_DIR = Path(__file__).resolve().parent
DEFAULT_JSON = APP_DIR / "faculty_network_v2.json"

app = Flask(__name__, static_folder="web", static_url_path="/web")
app.config["JSON_AS_ASCII"] = False

def _pick_position(node: Dict) -> Dict[str, float]:
    pos = node.get("position") or {}
    # JSONのキーが x3, y3 などの場合にも対応
    x = pos.get("x", pos.get("x3", pos.get("x2", 0.0)))
    y = pos.get("y", pos.get("y3", pos.get("y2", 0.0)))
    z = pos.get("z", pos.get("z3", 0.0))
    return {
        "x": float(x),
        "y": float(y),
        "z": float(z)
    }

def _normalize_node(node: Dict) -> Dict:
    fac_info = node.get("faculty_info", {})
    return {
        "id": node.get("id", ""),
        "name": node.get("name", ""),
        "affiliation": fac_info.get("affiliation", ""),
        "specialty": fac_info.get("specialty", ""),
        "message": fac_info.get("message", ""),
        "keywords": node.get("keywords", []),
        "main_course": node.get("main_course", ""),
        "cluster_id": node.get("cluster_id", 0),
        "position": _pick_position(node)
    }

def _normalize_edge(edge: Dict) -> Dict:
    return {
        "source": edge.get("source_id", ""),
        "target": edge.get("target_id", ""),
        "similarity": float(edge.get("similarity", 0.0))
    }

def load_network_data(json_path: Path) -> Dict:
    if not json_path.exists():
        return {"nodes": [], "edges": []}
    
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    nodes = [_normalize_node(node) for node in payload.get("nodes", [])]
    edges = [_normalize_edge(edge) for edge in payload.get("edges", [])]
    return {
        "nodes": nodes,
        "edges": edges,
    }

# 起動時に1回だけデータを読み込む
DATA = load_network_data(DEFAULT_JSON)

@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")

@app.get("/api/network")
def api_network():
    return jsonify(DATA)

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)