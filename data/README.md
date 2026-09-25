# Data Layout

役割ごとにデータを分離しています。詳細はリポジトリ直下の [README.md](../README.md) を参照してください。

| ディレクトリ | 役割 | 含めるもの / 含めないもの |
|---|---|---|
| `raw/` | 収集した生データ | RIDB・researchmap・シラバス・シーズ集のスクレイプ成果。原則そのまま保存 |
| `analysis/` | 意味空間・関係の計算 | 本文・キーワード・埋め込み・UMAP 座標・エッジ CSV。顔写真や表示用 URL は原則除外（`course_nodes.csv` など Display 向け中間表は例外） |
| `display/` | 3D Web UI 入力 | `display_data.json`（名前・所属・URL・説明・座標・エッジ） |

## サブディレクトリ

- `raw/researchmap/` — index・プロフィール・論文詳細・セッション cookie
- `raw/ridb/` — 教員名簿・シーズ taxonomy / membership
- `raw/syllabus/` — 一覧 CSV・詳細 CSV（`all_basic_info` 等）
- `analysis/` — `analysis_data.json`、embeddings、coords、field/course ノード・エッジ
- `display/` — `display_data.json`

## 再生成

Raw 更新後は次を順に実行します。

```text
pipeline/01_build_analysis_data.py
pipeline/02_build_field_tree.py
pipeline/03_embed_umap_fields.py
pipeline/04_build_course_relations.py
pipeline/05_export_display_data.py
```
