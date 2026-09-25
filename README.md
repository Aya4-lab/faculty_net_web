# faculty_net_web

大学内の**教員・講義・研究分野**の関係性を 3D 空間として可視化し、興味を起点に「え、ここ繋がるの？」という偶発的な発見を促す探索環境です。

単なる専門性の類似度マップではなく、**知識・研究・講義がどう結びついているか**を辿れるネットワーク空間の構築を目指しています。

---

## ビジョン

| やりたいこと | 例 |
|---|---|
| 興味から教員へ | シラバスのキーワード → 担当教員 → その研究分野 |
| 分野の横断発見 | 同じシーズ分野にいる別学類の教員 |
| 講義の再発見 | 同内容・複数クラスの講義を 1 ノードにまとめ、複数教員との関係を見る |
| 偶発的な接続 | 埋め込み空間上で近い別領域の講義・教員 |

Web UI（Three.js）では、大ノード（分野）・中ノード（教員）・小ノード（講義）と各種エッジを 3D で探索できます。

---

## システムアーキテクチャ & データ分離

データは役割ごとにディレクトリを分けています。

```
data/
  raw/        # 収集した生データ（スクレイピング成果）
  analysis/   # 意味空間・座標・中間エッジ（計算用）
  display/    # 3D UI が直接読む最終 JSON
```

### Raw Data（生データ）

収集ソースそのまま。加工前の正本。

| ソース | 主なファイル | 内容 |
|---|---|---|
| RIDB | `raw/ridb/ridb_yugo_faculty_*.json` | 教員名簿・所属・顔写真 URL・専門 |
| RIDB シーズ | `raw/ridb/seeds_taxonomy.json`, `seeds_membership.json` | 大分野 / 研究分野ツリーと所属教員 |
| researchmap | `raw/researchmap/kanazawa_researchers_index.json`, `remp_yugo_faculty_*.json`, `remp_yugo_paper_details_*.json` | プロフィール・論文メタ |
| シラバス | `raw/syllabus/kanazawa_syllabus.csv`, `all_basic_info.csv`, … | 一覧 URL（`lct_idx` 付き）・授業概要 |

### Analysis Data（意味空間計算用）

埋め込み・UMAP・関係抽出の入力。**顔写真・氏名表示用メタ・各種 URL は含めない**（テキストと ID・キーワード中心）。

| ファイル | 役割 |
|---|---|
| `analysis/analysis_data.json` | 教員 / 講義 / 論文の本文・キーワード |
| `analysis/*_embeddings.npz` | Sentence Transformers ベクトル |
| `analysis/*_coords*.json` | UMAP 3D 座標 |
| `analysis/field_tree.json`, `field_nodes.csv` | 分野ツリー |
| `analysis/course_nodes.csv` | 講義の表示用メタ（名前・シラバス URL 等） |

### Display Data（UI 表示用）

3D 画面で必要な属性をすべて載せた最終成果。

| ファイル | 役割 |
|---|---|
| `display/display_data.json` | ノード（名前・所属・URL・説明・座標）とエッジ |

`project_root/app_v2.py` は display をベースに、analysis 側の分野座標・`taught_by` エッジ・顔写真を実行時マージします。

### Relation Data（関係性）

| エッジ | ファイル / 型 | 意味 |
|---|---|---|
| 教員 → 講義 | `edges_professor_course.csv` (`taught_by`) | 担当 |
| 教員 → 研究分野 | `edges_professor_field.csv` (`belongs_to_field`) | シーズ所属 |
| 分野階層 | field ノードの `parent_id` / hierarchy エッジ | 大分野 ⊃ 研究分野 |
| 類似 | `display_data.json` 内 `similarity` | 埋め込み近傍 |

---

## ノード・エッジ構成（UI）

```
[大] 大分野 (major)          例: 情報学
  └─ [大] 研究分野 (field)   例: 知能情報学
       └─ [中] 教員 (professor)
            └─ [小] 講義 (course)   ※同内容クラスは 1 ノードに統合
```

- **大ノード**: シーズ集の大分野・研究分野
- **中ノード**: 教員（researchmap / RIDB リンク、顔写真）
- **小ノード**: 講義（シラバス詳細 URL。複数クラス時は `syllabus_urls`）
- **エッジ**: `taught_by` / `belongs_to_field` / 分野階層 / 類似度

論文ノードは analysis に保持していますが、現行 UI の主対象は分野・教員・講義です。

---

## ディレクトリ構成

```
faculty_net_web/
├── scraping/                 # 01 収集
│   ├── 01_ridb_scrape.py
│   ├── 02_build_researchmap_index.py
│   ├── 03_fetch_researchmap_profiles.py
│   ├── 04_fetch_paper_details.py
│   ├── 05_scrape_syllabus_list.py
│   ├── 06_scrape_syllabus_details.py
│   ├── 07_fetch_seeds_taxonomy.py
│   ├── 08_fetch_seeds_membership.py
│   ├── legacy_fetch_researcher_details.py   # 旧実装
│   └── legacy_fetch_paper_details.py        # 旧実装
├── pipeline/                 # 02〜05 整形〜表示
│   ├── syllabus_meta.py      # 共有ヘルパ（番号なし）
│   ├── 01_build_analysis_data.py
│   ├── 02_build_field_tree.py
│   ├── 03_embed_umap_fields.py
│   ├── 04_build_course_relations.py
│   └── 05_export_display_data.py
├── project_root/             # Flask + Three.js UI
│   ├── app_v2.py
│   └── web/index.html
├── data/
│   ├── raw/ | analysis/ | display/
│   └── README.md
├── requirements.txt
└── README.md
```

---

## パイプライン実行順

### A. データ収集（`scraping/`）— 初回・更新時

番号順に実行します（依存のあるものはスキップ不可）。

| # | スクリプト | 出力の要点 |
|---|---|---|
| 01 | `scraping/01_ridb_scrape.py` | RIDB 教員 JSON |
| 02 | `scraping/02_build_researchmap_index.py` | researchmap 金沢大 index |
| 03 | `scraping/03_fetch_researchmap_profiles.py` | 教員プロフィール + 論文 URL メタ |
| 04 | `scraping/04_fetch_paper_details.py` | 論文詳細（要旨等） |
| 05 | `scraping/05_scrape_syllabus_list.py` | シラバス一覧（`lct_idx` 付き URL） |
| 06 | `scraping/06_scrape_syllabus_details.py` | シラバス本文 CSV |
| 07 | `scraping/07_fetch_seeds_taxonomy.py` | シーズ分野ツリー |
| 08 | `scraping/08_fetch_seeds_membership.py` | 分野↔教員メンバーシップ |

researchmap はセッション cookie が必要な場合があります（環境変数 `RESEARCHMAP_SESSION_COOKIE`、または `data/raw/researchmap/researchmap_session_cookie.txt`）。

シラバス系は Edge + Selenium を使用します。

### B. 整形〜表示データ生成（`pipeline/`）— 毎回ここまで回せば UI 更新

| # | レイヤ | スクリプト | 内容 |
|---|---|---|---|
| 01 | 整形・分離 | `pipeline/01_build_analysis_data.py` | Raw → analysis テキスト。同内容講義の統合 |
| 02 | リレーション | `pipeline/02_build_field_tree.py` | 分野ツリー + `belongs_to_field` |
| 03 | 埋め込み・空間化 | `pipeline/03_embed_umap_fields.py` | Embedding + 共同 UMAP 座標 |
| 04 | リレーション | `pipeline/04_build_course_relations.py` | `taught_by` + course_nodes |
| 05 | UI 出力 | `pipeline/05_export_display_data.py` | `display_data.json` |

既存の埋め込みを再利用する場合:

```powershell
python pipeline/03_embed_umap_fields.py --reuse-embeddings
```

メタデータだけ差し替える場合:

```powershell
python pipeline/05_export_display_data.py --enrich-only
```

### C. Web UI 起動

```powershell
cd project_root
python app_v2.py
# → http://127.0.0.1:5001
```

---

## セットアップ

### 要件

- Python 3.11 以上（推奨）
- Windows での UMAP / numba 利用時は `requirements.txt` 末尾のピン留め注記を参照

### 仮想環境と依存関係

```powershell
cd C:\Users\sonan\dev\faculty_net_web
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m playwright install chromium   # researchmap スクレイプ用
```

主なライブラリ: Flask, BeautifulSoup4, Selenium, sentence-transformers, umap-learn, scikit-learn, pandas, Playwright（researchmap）。

### フル再生成の最短コマンド例

Raw が既にある場合（分析〜表示のみ）:

```powershell
cd C:\Users\sonan\dev\faculty_net_web
.\.venv\Scripts\Activate.ps1
python pipeline/01_build_analysis_data.py
python pipeline/02_build_field_tree.py
python pipeline/03_embed_umap_fields.py --reuse-embeddings
python pipeline/04_build_course_relations.py
python pipeline/05_export_display_data.py
cd project_root
python app_v2.py
```

---

## 設計メモ（実装上のポイント）

- **researchmap 氏名マッチ**: 正規化後の完全一致。姓だけの部分一致は禁止（誤紐付け防止）。必要に応じて URL override / 会員 ID を使用。
- **シラバス URL**: 一覧スクレイプの `日URL`（`lct_cd` + `lct_idx`）を display まで引き継ぐ。不完全な `lct_no` 再構築はしない。
- **講義統合**: 科目名 + 授業概要等の正規化テキストが一致するものを 1 ノードに集約し、複数教員へ `taught_by`、複数 URL は `syllabus_urls` に保持。

---

## Legacy スクリプト

| ファイル | 代わりに使うもの |
|---|---|
| `scraping/legacy_fetch_researcher_details.py` | `03_fetch_researchmap_profiles.py` |
| `scraping/legacy_fetch_paper_details.py` | `04_fetch_paper_details.py` |

新規実行では legacy を使わないでください。
