# faculty_net_web

researchmap の「金沢大学」検索結果を一覧化し、RIDB 名簿と照合して研究者詳細を取得するためのスクリプト群です。

## 構成

- `scraping/build_kanazawa_index.py`  
  researchmap の検索結果をページネーションで巡回して、`kanazawa_researchers_index.json` を作成します。
- `scraping/fetch_researcher_details.py`  
  RIDB の名簿と index を照合し、該当する研究者だけ詳細と論文情報を取得します。
- `scraping/batch_researchmap_scrape.py`  
  旧互換ラッパーです。新しい実行は前の 2 つを使ってください。

## 必要なもの

- Python 3.11 以上
- `requirements.txt` の依存関係
- researchmap にアクセスできるセッション cookie

cookie は次のどちらかで指定できます。

- 環境変数 `RESEARCHMAP_SESSION_COOKIE`
- `scraping/researchmap_session_cookie.txt` に `cookie_name=cookie_value` 形式で 1 行保存

## セットアップ

```powershell
cd C:\Users\sonan\dev\faculty_net_web
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Step 1: 一覧インデックス化

`金沢大学` の検索結果を最後のページまで巡回して index を作成します。

```powershell
cd C:\Users\sonan\dev\faculty_net_web\scraping
$env:RESEARCHMAP_SESSION_COOKIE='cookie_name=cookie_value'
c:\Users\sonan\dev\faculty_net_web\.venv\Scripts\python.exe build_kanazawa_index.py --query "金沢大学" --out kanazawa_researchers_index.json
```

安全のためページ数を制限したい場合は `--pages 2` のように指定できます。

## Step 2: 照合して詳細取得

### 今回の 49 名だけ取得する場合

RIDB の `ridb_yugo_faculty_ja.json` と `ridb_yugo_faculty_en.json` を index と照合し、該当者だけを取得します。

```powershell
cd C:\Users\sonan\dev\faculty_net_web\scraping
$env:RESEARCHMAP_SESSION_COOKIE='cookie_name=cookie_value'
c:\Users\sonan\dev\faculty_net_web\.venv\Scripts\python.exe fetch_researcher_details.py --index kanazawa_researchers_index.json --json-ja ridb_yugo_faculty_ja.json --json-en ridb_yugo_faculty_en.json --out-ja remp_yugo_faculty_ja.json --out-en remp_yugo_faculty_en.json
```

### 将来の 2,000 名規模で分割取得する場合

`ridb_faculty_ja.json` を使い、100 件単位などで `--start-idx` と `--limit` を切りながら実行します。

```powershell
cd C:\Users\sonan\dev\faculty_net_web\scraping
$env:RESEARCHMAP_SESSION_COOKIE='cookie_name=cookie_value'
c:\Users\sonan\dev\faculty_net_web\.venv\Scripts\python.exe fetch_researcher_details.py --index kanazawa_researchers_index.json --json-ja ridb_faculty_ja.json --single-source --start-idx 0 --limit 100 --out-ja remp_faculty_0001_0100_ja.json --out-en remp_faculty_0001_0100_en.json
```

同じ要領で `--start-idx 100 --limit 100` のように分割して回してください。

## 出力

- `kanazawa_researchers_index.json`
  - `name`
  - `affiliation`
  - `profile_url`
- `remp_yugo_faculty_ja.json` / `remp_yugo_faculty_en.json`
  - 名前
  - researchmap URL
  - 研究キーワード
  - 研究分野
  - 所属学会
  - 学歴
  - 経歴
  - 論文

## 注意

- researchmap 側の仕様変更やアクセス制御により、cookie がない場合は 403 になることがあります。
- `batch_researchmap_scrape.py` は残していますが、今後は使わずに Step 1 / Step 2 を直接実行する運用を推奨します。