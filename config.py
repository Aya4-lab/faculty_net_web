"""
設定ファイル: パラメータを簡単に調整できます
"""

# 入力・出力ファイルパス
INPUT_JSONL = 'yugo_faculty_merged_v2.jsonl'  # faculty_info + researchmap + syllabus の統合データ
OUTPUT_JSON = 'faculty_network_v2.json'

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
