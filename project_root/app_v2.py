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

def load_network_data(json_path: Path):
    if not json_path.exists():
        return {"nodes": [], "edges": []}
    
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    
    # 02_analyze_umap.py の出力フォーマットに合わせて正規化
    normalized_nodes = []
    for node in payload.get("nodes", []):
        pos = node.get("position", [0.0, 0.0, 0.0])
        # [x, y, z] 配列をフロントエンドが解釈しやすい {x, y, z} に変換
        if isinstance(pos, list) and len(pos) >= 3:
            pos_dict = {"x": float(pos[0]), "y": float(pos[1]), "z": float(pos[2])}
        else:
            pos_dict = {"x": float(pos.get("x", 0)), "y": float(pos.get("y", 0)), "z": float(pos.get("z", 0))}
            
        normalized_nodes.append({
            "id": node.get("id", ""),
            "type": node.get("type", "unknown"),
            "name": node.get("id", ""),  # 現時点ではIDを仮名として表示
            "text_preview": node.get("text_preview", ""),
            "position": pos_dict
        })
        
    normalized_edges = []
    for edge in payload.get("edges", []):
        normalized_edges.append({
            "source": edge.get("source", edge.get("source_id", "")),
            "target": edge.get("target", edge.get("target_id", "")),
            "type": edge.get("type", "similarity"),
            "similarity": float(edge.get("similarity", 0.0))
        })
        
    return {
        "nodes": normalized_nodes,
        "edges": normalized_edges
    }

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