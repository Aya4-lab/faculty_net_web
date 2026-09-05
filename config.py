"""
設定ファイル: パラメータとデータ配置をまとめて管理します。
"""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
ANALYSIS_DATA_DIR = DATA_DIR / "analysis"
DISPLAY_DATA_DIR = DATA_DIR / "display"

RAW_RESEARCHMAP_DIR = RAW_DATA_DIR / "researchmap"
RAW_RIDB_DIR = RAW_DATA_DIR / "ridb"
RAW_SYLLABUS_DIR = RAW_DATA_DIR / "syllabus"

LEGACY_INPUT_JSONL = PROJECT_ROOT / "yugo_faculty_merged_v2.jsonl"
INPUT_JSONL = RAW_DATA_DIR / "yugo_faculty_merged_v2.jsonl"
if not INPUT_JSONL.exists() and LEGACY_INPUT_JSONL.exists():
    INPUT_JSONL = LEGACY_INPUT_JSONL

NETWORK_JSON = DISPLAY_DATA_DIR / "faculty_network_v2.json"
LEGACY_NETWORK_JSON = PROJECT_ROOT / "faculty_network_v2.json"

RESEARCHMAP_INDEX_JSON = RAW_RESEARCHMAP_DIR / "kanazawa_researchers_index.json"
RESEARCHMAP_JA_JSON = RAW_RESEARCHMAP_DIR / "remp_yugo_faculty_ja.json"
RESEARCHMAP_EN_JSON = RAW_RESEARCHMAP_DIR / "remp_yugo_faculty_en.json"
RESEARCHMAP_COOKIE_FILE = RAW_RESEARCHMAP_DIR / "researchmap_session_cookie.txt"

# BERTモデル設定
BERT_MODEL = 'sonoisa/sentence-bert-base-ja-mean-tokens-v2'

# UMAP（次元削減）パラメータ
UMAP_N_NEIGHBORS = 15  # 近傍点の数（5-50）: 小さいほど局所構造を重視
UMAP_MIN_DIST = 0.1    # 最小距離（0.0-0.99）: 小さいほどクラスタが密集

# DBSCANクラスタリングパラメータ
DBSCAN_EPS = 0.50          # 近傍半径（0.3-1.0）: 小さいほどクラスタが細かく分かれる
DBSCAN_MIN_SAMPLES = 3     # 最小サンプル数（2-10）: コアポイントになるための最小点数

# 類似度エッジの閾値
SIMILARITY_THRESHOLD = 0.83  # コサイン類似度の閾値（0.5-0.9）
                             # 推奨値:
                             # - 0.65: 約60エッジ（適度なつながり）
                             # - 0.70: 約25エッジ（重要なつながりのみ）
                             # - 0.60: 約138エッジ（多めのつながり）

# TF-IDFキーワード抽出
TOP_KEYWORDS_PER_CLUSTER = 5  # 各クラスターから抽出するキーワード数


def ensure_data_directories() -> None:
    """Create the data directory tree used by the project."""
    for path in [
        RAW_DATA_DIR,
        ANALYSIS_DATA_DIR,
        DISPLAY_DATA_DIR,
        RAW_RESEARCHMAP_DIR,
        RAW_RIDB_DIR,
        RAW_SYLLABUS_DIR,
    ]:
        path.mkdir(parents=True, exist_ok=True)
