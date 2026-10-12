# 3D Web Knowledge Exploration Space (知識空間探索Webアプリ)

大学内の「研究」「講義」「研究分野」等の関係性を3D Web空間上に構造化し、ユーザーが自身の興味を起点として直感的に歩き回り、「意外なつながり（『おっ』という発見）」を得るためのインタラクティブな知識探索環境です。

## 🌟 主な機能

- **3D空間探索**: UMAPとEmbedding（Sentence Transformers）により空間配置された教員・講義・分野ノードのビジュアル探索。
- **2つの探索モード**:
  - **キーワード検索モード**: 直感的な単語検索とノード間の関係性ハイライト。
  - **思考・文章探索モード（引力と波紋モデル）**: 自由記述文から「自分ノード」を出現させ、関連ノードを引き寄せるインタラクティブ演出。
- **インタラクティブエッジ**: ノード間を繋ぐ点線（エッジ）へ触れることで、接続理由（類似度や共通キーワード）を表示。
- **発見度アンケート機能**: ユーザーの体験（「おっ」の発生度）を計測・記録するフィードバックモーダル。

## 🛠 テクノロジー・スタック

- **フロントエンド**: Three.js, JavaScript (ES6+), HTML5/CSS3
- **バックエンド**: Python, Flask, Flask-CORS, Gunicorn
- **データ分析・機械学習**: Sentence Transformers (Embedding), UMAP-learn, Pandas, NumPy

## 🚀 ローカル環境での起動手順

1. **リポジトリのクローン**

   ```bash
   git clone https://github.com/your-username/your-repo-name.git
   cd your-repo-name
   ```

2. **仮想環境の構築とライブラリのインストール**

   ```bash
   python -m venv venv
   source venv/bin/activate  # Windowsの場合: venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. **環境変数の設定**

   `.env.example` をコピーして `.env` を作成し、必要な設定を行ってください。

   ```bash
   cp .env.example .env   # Windowsの場合: copy .env.example .env
   ```

   | 変数 | 説明 |
   |---|---|
   | `BASIC_AUTH_ENABLED` | `1` で Basic 認証を有効化（共有・本番向け）。ローカル既定は `0` |
   | `BASIC_AUTH_USER` / `BASIC_AUTH_PASSWORD` | Basic 認証のユーザー名とパスワード |
   | `PORT` | 起動ポート（既定 `5001`） |

4. **Flaskアプリの実行**

   ```bash
   cd project_root
   python app_v2.py
   ```

   ブラウザで `http://localhost:5001` にアクセスしてください。

   本番相当（gunicorn）で起動する場合は、リポジトリルートから次を実行します。

   ```bash
   gunicorn app:app --bind 0.0.0.0:5001 --workers 1 --timeout 180
   ```

## 📁 ディレクトリ構造

```text
faculty_net_web/
├── project_root/          # Flask API + Three.js UI
│   ├── app_v2.py          # バックエンドAPIおよびルートハンドラー
│   ├── explore_engine.py  # 探索（キーワード / 文章）ロジック
│   └── web/               # フロントエンド（HTML / CSS / JS）
├── data/                  # Raw / Analysis / Display に分類されたデータ
├── scraping/              # データ収集スクリプト
├── pipeline/              # 埋め込み・UMAP・表示データ生成
├── docs/DEVELOPMENT.md    # パイプライン詳細・設計メモ
├── app.py                 # 本番用 WSGI エントリ（gunicorn app:app）
├── .env.example
└── requirements.txt
```

データ収集・再生成パイプラインの詳細は [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) を参照してください。
