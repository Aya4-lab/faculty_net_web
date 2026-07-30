"""
教員ネットワーク可視化用のJSONデータ生成スクリプト

このスクリプトは以下の処理を行います：
1. BERT埋め込みベクトルの計算（高次元）
2. 次元削減（UMAP）で3D座標を生成
3. DBSCANでクラスタリング
4. TF-IDFでクラスターのキーワード分析
5. 高次元ベクトルを使った類似度計算とエッジリスト作成
6. 最終JSONのエクスポート
"""

import sys
# PyTorch/Transformersのエラー回避用のダミー関数
if not hasattr(sys, 'get_int_max_str_digits'):
    sys.get_int_max_str_digits = lambda: 4300
    sys.set_int_max_str_digits = lambda x: None
    
import json
import pandas as pd
import numpy as np
from sentence_transformers import SentenceTransformer
from transformers import AutoModel, AutoTokenizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.cluster import DBSCAN
from sklearn.feature_extraction.text import TfidfVectorizer
import umap
from collections import defaultdict
from tqdm import tqdm
import torch


def load_faculty_data(jsonl_path='yugo_faculty_merged_v2.jsonl'):
    """
    教員データをJSONLファイルから読み込む
    
    Args:
        jsonl_path: JSONLファイルのパス
    
    Returns:
        list: 教員データのリスト
    """
    print(f"📖 教員データを読み込んでいます: {jsonl_path}")
    faculty_data = []
    with open(jsonl_path, 'r', encoding='utf-8') as f:
        for line in f:
            faculty_data.append(json.loads(line))
    print(f"✅ {len(faculty_data)}名の教員データを読み込みました")
    return faculty_data


def create_text_for_bert(faculty):
    """
    各教員のテキスト情報を結合してBERT入力用のテキストを作成
    ※ BERTの意味解釈を活かすため、名前、所属、学歴などの属性情報を除外。
    研究内容や思想を反映する文章・キーワード群に特化させる。
    """
    text_parts = []
    
    # ===== faculty_info からの情報 =====
    faculty_info = faculty.get('faculty_info', {})
    if faculty_info:
        # 名前や所属は除外し、専門性やスタンスが表れる項目に絞る
        for key in ['専門', 'キーワード', '探究エリア', 'メッセージ']:
            if faculty_info.get(key):
                text_parts.append(str(faculty_info[key]))
    
    # ===== researchmap からの情報 =====
    researchmap = faculty.get('researchmap', {})
    if researchmap and researchmap.get('found'):
        # 研究キーワードと分野
        if researchmap.get('research_keywords'):
            keywords = [k for k in researchmap['research_keywords'] if isinstance(k, str)]
            text_parts.append(' '.join(keywords))
        
        if researchmap.get('research_areas'):
            areas = [a for a in researchmap['research_areas'] if isinstance(a, str)]
            text_parts.append(' '.join(areas))
        
        # 論文タイトルと科研費タイトル（具体的な研究内容を示すため極めて重要）
        if researchmap.get('papers'):
            papers = [p for p in researchmap['papers'] if isinstance(p, str)]
            text_parts.append(' '.join(papers))
        
        if researchmap.get('kaken_grants'):
            grants = [g for g in researchmap['kaken_grants'] if isinstance(g, str)]
            text_parts.append(' '.join(grants))
            
        # ※ coauthors(共著者), education(学歴), association_memberships(所属学会) は
        # 人名や固有名詞のノイズになるためBERT入力からは除外
    
    # ===== syllabus からの情報 =====
    syllabus = faculty.get('syllabus', {})
    if syllabus:
        # basic_info から
        if syllabus.get('basic_info'):
            for course in syllabus['basic_info']:
                if isinstance(course, dict):
                    # 授業内容に関する記述を抽出
                    if course.get('科目名'):
                        text_parts.append(str(course['科目名']))
                    if course.get('キーワード') and str(course['キーワード']) != '-----':
                        text_parts.append(str(course['キーワード']))
                    if course.get('授業の主題'):
                        text_parts.append(str(course['授業の主題']))
                    if course.get('学習目標'):
                        text_parts.append(str(course['学習目標']))
                    if course.get('授業概要') and str(course['授業概要']) != '特になし':
                        text_parts.append(str(course['授業概要']))
        
        # schedule から講義内容
        if syllabus.get('schedule'):
            for lecture in syllabus['schedule']:
                if isinstance(lecture, dict):
                    if lecture.get('テーマ') and pd.notna(lecture['テーマ']):
                        text_parts.append(str(lecture['テーマ']))
                    if lecture.get('具体的な内容') and pd.notna(lecture['具体的な内容']):
                        text_parts.append(str(lecture['具体的な内容']))
    
    # すべてのテキストを結合（空文字列や None を除外）
    clean_text_parts = [str(part) for part in text_parts if part and pd.notna(part)]
    full_text = ' '.join(clean_text_parts)
    return full_text


def compute_bert_embeddings(faculty_data, model_name='sonoisa/sentence-bert-base-ja-mean-tokens-v2'):
    """
    各教員のBERT埋め込みベクトルを計算
    
    Args:
        faculty_data: 教員データのリスト
        model_name: 使用するSentence-BERTモデル名
    
    Returns:
        numpy.ndarray: 埋め込みベクトルの行列 (n_teachers × embedding_dim)
    """
    print(f"\n🤖 BERTモデルを読み込んでいます: {model_name}")
    try:
        model = SentenceTransformer(model_name)
        use_sentence_transformers = True
    except Exception as e:
        print(f"⚠️  SentenceTransformer の読み込みに失敗しました: {e}")
        print("   AutoTokenizer + AutoModel でフォールバックします")
        tokenizer = AutoTokenizer.from_pretrained(model_name)
        transformer_model = AutoModel.from_pretrained(model_name)
        transformer_model.eval()
        use_sentence_transformers = False
    
    print("📝 各教員のテキストを作成しています...")
    texts = [create_text_for_bert(faculty) for faculty in faculty_data]
    
    print("🔢 BERT埋め込みベクトルを計算しています...")
    if use_sentence_transformers:
        embeddings = model.encode(texts, show_progress_bar=True, batch_size=16)
    else:
        embeddings = []
        batch_size = 16
        for start_index in tqdm(range(0, len(texts), batch_size)):
            batch_texts = texts[start_index:start_index + batch_size]
            encoded = tokenizer(
                batch_texts,
                padding=True,
                truncation=True,
                return_tensors='pt'
            )
            with torch.no_grad():
                outputs = transformer_model(**encoded)
                token_embeddings = outputs.last_hidden_state
                attention_mask = encoded['attention_mask'].unsqueeze(-1)
                masked_embeddings = token_embeddings * attention_mask
                summed = masked_embeddings.sum(dim=1)
                counts = attention_mask.sum(dim=1).clamp(min=1)
                batch_embeddings = (summed / counts).cpu().numpy()
                embeddings.append(batch_embeddings)
        embeddings = np.vstack(embeddings)
    
    print(f"✅ 埋め込みベクトルの計算完了: {embeddings.shape}")
    return embeddings


def reduce_to_3d(embeddings, n_neighbors=15, min_dist=0.1, random_state=42):
    """
    高次元ベクトルをUMAPで3次元に次元削減
    
    Args:
        embeddings: 高次元埋め込みベクトル
        n_neighbors: UMAPのn_neighborsパラメータ
        min_dist: UMAPのmin_distパラメータ
        random_state: 乱数シード
    
    Returns:
        numpy.ndarray: 3次元座標 (n_teachers × 3)
    """
    print("\n🗺️  UMAPで3次元に次元削減しています...")
    reducer = umap.UMAP(
        n_components=3,
        n_neighbors=n_neighbors,
        min_dist=min_dist,
        random_state=random_state,
        metric='cosine'
    )
    coords_3d = reducer.fit_transform(embeddings)
    print(f"✅ 3次元座標の生成完了: {coords_3d.shape}")
    return coords_3d


def cluster_with_dbscan(coords_3d, eps=0.50, min_samples=3):
    """
    3D座標を使ってDBSCANでクラスタリング
    
    Args:
        coords_3d: 3次元座標
        eps: DBSCANのepsパラメータ
        min_samples: DBSCANのmin_samplesパラメータ
    
    Returns:
        numpy.ndarray: クラスターラベル
    """
    print("\n🎯 DBSCANでクラスタリングしています...")
    clustering = DBSCAN(eps=eps, min_samples=min_samples)
    cluster_labels = clustering.fit_predict(coords_3d)
    
    n_clusters = len(set(cluster_labels)) - (1 if -1 in cluster_labels else 0)
    n_noise = list(cluster_labels).count(-1)
    
    print(f"✅ クラスタリング完了:")
    print(f"   - クラスター数: {n_clusters}")
    print(f"   - ノイズポイント: {n_noise}")
    
    return cluster_labels


def extract_cluster_keywords(faculty_data, cluster_labels, top_n=5):
    """
    各クラスターの特徴的なキーワードをTF-IDFで抽出
    
    Args:
        faculty_data: 教員データのリスト
        cluster_labels: クラスターラベル
        top_n: 各クラスターから抽出するキーワード数
    
    Returns:
        dict: {cluster_id: [keyword1, keyword2, ...]}
    """
    import re
    
    print("\n🔍 各クラスターのキーワードを抽出しています...")
    
    # クラスターごとにテキストを集約
    cluster_texts = defaultdict(list)
    for i, (faculty, label) in enumerate(zip(faculty_data, cluster_labels)):
        if label != -1:  # ノイズポイントは除外
            text = create_text_for_bert(faculty)
            cluster_texts[int(label)].append(text)
    
    # 各クラスターのテキストを結合
    cluster_documents = {}
    for cluster_id, texts in cluster_texts.items():
        cluster_documents[cluster_id] = ' '.join(texts)
    
    if not cluster_documents:
        print("⚠️  有効なクラスターが見つかりませんでした")
        return {}
    
    # TF-IDFで特徴的な単語を抽出
    vectorizer = TfidfVectorizer(
        max_features=1000,
        min_df=1,
        max_df=0.8,
        ngram_range=(1, 2)
    )
    
    cluster_ids = list(cluster_documents.keys())
    documents = [cluster_documents[cid] for cid in cluster_ids]
    
    tfidf_matrix = vectorizer.fit_transform(documents)
    feature_names = vectorizer.get_feature_names_out()
    
    # 各クラスターのトップキーワードを抽出（フィルタリング付き）
    cluster_keywords = {}
    for i, cluster_id in enumerate(cluster_ids):
        tfidf_scores = tfidf_matrix[i].toarray()[0]
        
        # スコアの高い順にソート
        sorted_indices = tfidf_scores.argsort()[::-1]
        
        # フィルタリングしながら上位キーワードを選択
        keywords = []
        for idx in sorted_indices:
            if len(keywords) >= top_n:
                break
            
            word = feature_names[idx]
            
            # フィルタリング: 数字のみ、短すぎる、意味のない単語を除外
            if (len(word) >= 2 and
                not re.match(r'^[\d\-\.:：]+$', word) and  # 数字と時刻記号のみを除外
                not re.match(r'^\d+\s+\d+$', word) and  # "17 00" のようなパターンを除外
                not re.match(r'^\d{2}\s*:\s*\d{2}$', word) and  # "17:00" を除外
                not re.match(r'^\d+', word) and  # 数字で始まる単語を除外（"00 dcap"など）
                ' ' not in word and  # スペースを含むフレーズを除外
                not word in ['nan', 'NaN', 'null', '-----', 'まとめ']):  # 意味のない単語も除外
                keywords.append(word)
        
        cluster_keywords[cluster_id] = keywords
        print(f"   Cluster {cluster_id}: {', '.join(keywords)}")
    
    print("✅ キーワード抽出完了")
    return cluster_keywords


def compute_similarity_edges(embeddings, faculty_data, threshold=0.8):
    """
    高次元ベクトルを使って類似度を計算し、閾値以上のペアをエッジリストとして返す
    
    Args:
        embeddings: 高次元埋め込みベクトル
        faculty_data: 教員データのリスト
        threshold: 類似度の閾値
    
    Returns:
        list: エッジリスト [{"source_id": "id1", "target_id": "id2", "similarity": 0.85}, ...]
    """
    print(f"\n🔗 類似度エッジを計算しています（閾値: {threshold}）...")
    
    # コサイン類似度行列を計算
    sim_matrix = cosine_similarity(embeddings)
    
    edges = []
    n = len(faculty_data)
    
    # 上三角行列のみを処理（i < j）
    for i in tqdm(range(n), desc="エッジ計算"):
        for j in range(i + 1, n):
            similarity = float(sim_matrix[i, j])
            if similarity >= threshold:
                edges.append({
                    "source_id": f"teacher_{i:03d}",
                    "target_id": f"teacher_{j:03d}",
                    "similarity": round(similarity, 4)
                })
    
    print(f"✅ {len(edges)}個のエッジを抽出しました")
    return edges


def extract_top_keywords(faculty, top_n=5):
    """
    全データから頻出キーワードを抽出（人名は最大1つまで）
    
    Args:
        faculty: 教員データ
        top_n: 抽出するキーワード数
    
    Returns:
        list: 頻出キーワードリスト
    """
    from collections import Counter
    import re
    
    all_text = []
    
    # faculty_info から
    faculty_info = faculty.get('faculty_info', {})
    if faculty_info.get('キーワード'):
        keywords = str(faculty_info['キーワード']).split('/')
        all_text.extend([kw.strip() for kw in keywords if kw.strip()])
    
    # researchmap から
    researchmap = faculty.get('researchmap', {})
    if researchmap and researchmap.get('found'):
        if researchmap.get('research_keywords'):
            all_text.extend([k.strip() for k in researchmap['research_keywords'] 
                           if isinstance(k, str) and k.strip()])
        
        # 研究分野からもキーワードを抽出（専門用語が多い）
        if researchmap.get('research_areas'):
            for area in researchmap['research_areas']:
                if isinstance(area, str):
                    # "情報通信 / 知能ロボティクス /" のような形式から専門用語を抽出
                    parts = re.split(r'[/／]', area)
                    for part in parts:
                        clean_part = part.strip()
                        if clean_part and len(clean_part) > 2:
                            all_text.append(clean_part)
    
    # syllabus から（授業の主題や学習目標から専門用語を抽出）
    syllabus = faculty.get('syllabus', {})
    if syllabus and syllabus.get('basic_info'):
        for course in syllabus['basic_info']:
            if isinstance(course, dict):
                # 科目名から専門用語を抽出
                course_name = course.get('科目名', '')
                if course_name:
                    # [英語名]を除去
                    jp_name = course_name.split('[')[0].strip()
                    if jp_name and len(jp_name) > 2:
                        all_text.append(jp_name)
                
                # キーワードフィールドから
                kw = str(course.get('キーワード', ''))
                if kw and kw != '-----' and '【' not in kw:
                    # 意味のある単語を抽出（全角カンマ、読点、半角カンマ、スラッシュで分割）
                    words = re.split(r'[、，,/\s]+', kw)
                    for word in words:
                        word = word.strip()
                        # フィルタリング: 数字のみ、記号のみ、短すぎる単語を除外
                        if (word and 
                            len(word) > 2 and 
                            not re.match(r'^[\d\-\.]+$', word) and  # 数字と記号のみを除外
                            not re.match(r'^[\d\-\.]+回[）)]?$', word) and  # "7回)" のようなパターンを除外
                            not re.match(r'^\d+-\d+\.?$', word) and  # "1-1." のようなパターンを除外
                            not word.endswith('回)') and
                            not word.endswith('回）')):
                            all_text.append(word)
    
    def is_person_name(word):
        """人名かどうかを判定（姓名の構造: 漢字 スペース 漢字）"""
        # 全角または半角スペースで区切られた2つの漢字グループ
        if re.match(r'^[\u4e00-\u9fff]+[\s　]+[\u4e00-\u9fff]+$', word):
            return True
        return False
    
    # カウントして上位を抽出
    # さらにフィルタリング
    filtered_words = []
    for word in all_text:
        word = word.strip()
        # 意味のあるキーワードのみ抽出
        if (word and 
            len(word) >= 2 and  # 最低2文字以上
            not re.match(r'^[\d\-\.]+$', word) and  # 数字と記号のみを除外
            not re.match(r'^[\(\)（）\[\]【】]+$', word) and  # 括弧のみを除外
            not word in ['回)', '7回)', 'nan', 'NaN', 'null'] and
            not re.match(r'^\d+回[）)]?$', word)):  # "7回)" を除外
            filtered_words.append(word)
    
    word_counts = Counter(filtered_words)
    
    # 頻度順に抽出（人名は最大1つまで）
    top_keywords = []
    person_count = 0
    
    for word, count in word_counts.most_common():
        # 人名の場合、すでに1つあればスキップ
        if is_person_name(word):
            if person_count >= 1:
                continue
            person_count += 1
        
        top_keywords.append(word)
        
        if len(top_keywords) >= top_n:
            break
    
    # キーワードが足りない場合、faculty_infoの専門を追加
    if len(top_keywords) < top_n and faculty_info.get('専門'):
        specialty = faculty_info['専門']
        if specialty and specialty not in top_keywords:
            top_keywords.append(specialty)
    
    return top_keywords[:top_n]


def get_main_course(faculty):
    """
    担当科目を優先順位に従って取得
    
    優先順位:
    1. Q2 かつ 所属が同じ
    2. 全開講期 かつ 所属が同じ
    3. 一番上
    4. null
    
    Args:
        faculty: 教員データ
    
    Returns:
        str or None: 科目名
    """
    syllabus = faculty.get('syllabus', {})
    if not syllabus or not syllabus.get('basic_info'):
        return None
    
    faculty_info = faculty.get('faculty_info', {})
    faculty_affiliation = faculty_info.get('所属', '')
    
    courses = syllabus['basic_info']
    if not courses:
        return None
    
    # 優先順位1: Q2 かつ 所属が同じ
    for course in courses:
        if isinstance(course, dict):
            course_name = course.get('科目名', '')
            # Q2を含むかチェック（科目ナンバーやシラバス情報から判定）
            course_number = course.get('科目ナンバー', '')
            if 'Q2' in course_number or '第2' in str(course.get('開講期', '')):
                # 所属チェック（科目名や担当教員情報から推測）
                if faculty_affiliation in course_name or faculty_affiliation in str(course):
                    return course_name.split('[')[0].strip()
    
    # 優先順位2: 所属が同じ
    for course in courses:
        if isinstance(course, dict):
            course_name = course.get('科目名', '')
            if faculty_affiliation and faculty_affiliation in course_name:
                return course_name.split('[')[0].strip()
    
    # 優先順位3: 一番上
    if courses[0] and isinstance(courses[0], dict):
        course_name = courses[0].get('科目名', '')
        if course_name:
            return course_name.split('[')[0].strip()
    
    return None


def get_coauthor(faculty):
    """
    共著者を1人取得（フルネーム、更新日を除去）
    
    Args:
        faculty: 教員データ
    
    Returns:
        str or None: 共著者名（フルネーム）
    """
    import re
    
    researchmap = faculty.get('researchmap', {})
    if researchmap and researchmap.get('found'):
        coauthors = researchmap.get('coauthors', [])
        if coauthors and len(coauthors) > 0:
            coauthor = coauthors[0]
            if isinstance(coauthor, dict):
                name = coauthor.get('name', '')
                if name:
                    # 様々な日付パターンを除去
                    # "10/08 更新", "2024/", "2024/10/08" など
                    name = re.sub(r'\s*\d{1,4}[/\-]\d{0,2}[/\-]?\d{0,2}.*$', '', name).strip()
                    # "更新" で終わるパターンも除去
                    name = re.sub(r'\s*更新.*$', '', name).strip()
                    return name if name else None
    return None


def get_similar_teachers(teacher_id, edges, faculty_data, top_n=2):
    """
    類似度が高い教員を取得（融合50人の中から）
    
    Args:
        teacher_id: 教員ID
        edges: エッジリスト
        faculty_data: 教員データのリスト
        top_n: 取得する人数
    
    Returns:
        list: 類似教員の名前リスト
    """
    # このteacher_idに関連するエッジを取得
    related_edges = []
    for edge in edges:
        if edge['source_id'] == teacher_id:
            related_edges.append((edge['target_id'], edge['similarity']))
        elif edge['target_id'] == teacher_id:
            related_edges.append((edge['source_id'], edge['similarity']))
    
    # 類似度でソート
    related_edges.sort(key=lambda x: x[1], reverse=True)
    
    # 上位n人の名前を取得
    similar_names = []
    for related_id, similarity in related_edges[:top_n]:
        # IDから名前を取得
        idx = int(related_id.split('_')[1])
        if idx < len(faculty_data):
            faculty = faculty_data[idx]
            name = (faculty.get('faculty_info', {}).get('名前_日本語') or 
                   faculty.get('researchmap', {}).get('name') or
                   faculty.get('name', ''))
            if name:
                similar_names.append(name)
    
    return similar_names


def create_final_json(faculty_data, embeddings, coords_3d, cluster_labels, 
                     cluster_keywords, edges, output_path='faculty_network_v2.json'):
    """
    最終的なJSONファイルを生成
    
    Args:
        faculty_data: 教員データのリスト
        embeddings: 高次元埋め込みベクトル
        coords_3d: 3次元座標
        cluster_labels: クラスターラベル
        cluster_keywords: クラスターのキーワード
        edges: エッジリスト
        output_path: 出力JSONファイルのパス
    """
    print(f"\n💾 最終JSONを生成しています: {output_path}")
    
    # ノードデータの作成
    nodes = []
    for i, (faculty, coords, cluster_id) in enumerate(
        zip(faculty_data, coords_3d, cluster_labels)
    ):
        # 基本情報の取得
        faculty_info = faculty.get('faculty_info', {})
        researchmap = faculty.get('researchmap', {})
        
        # 名前の取得
        name = (faculty_info.get('名前_日本語') or 
                researchmap.get('name') or
                faculty.get('name', f'教員{i+1}'))
        
        # 教員IDの生成
        teacher_id = f"teacher_{i:03d}"
        
        # 類似度が高い教員を取得
        similar_teachers = get_similar_teachers(teacher_id, edges, faculty_data, top_n=2)
        
        # 構造化された情報
        node = {
            "id": teacher_id,
            "name": name,
            "faculty_info": {
                "affiliation": faculty_info.get('所属', ''),
                "specialty": faculty_info.get('専門', ''),
                "message": faculty_info.get('メッセージ', ''),
                "photo_url": faculty_info.get('顔写真URL', '')
            },
            "keywords": extract_top_keywords(faculty, top_n=5),
            "main_course": get_main_course(faculty),
            "coauthor": get_coauthor(faculty),
            "similar_teachers": similar_teachers,
            "position": {
                "x": float(coords[0]),
                "y": float(coords[1]),
                "z": float(coords[2])
            },
            "cluster_id": int(cluster_id),
            "vector": embeddings[i].tolist(),
            "profile_url": researchmap.get('profile_url', '') if researchmap else ''
        }
        nodes.append(node)
    
    # クラスター情報の作成（3D座標情報を追加）
    clusters = []
    for cluster_id, keywords in cluster_keywords.items():
        # このクラスターに属する教員の座標を取得
        cluster_coords = []
        cluster_member_ids = []
        for i, (coords, label) in enumerate(zip(coords_3d, cluster_labels)):
            if int(label) == cluster_id:
                cluster_coords.append(coords)
                cluster_member_ids.append(f"teacher_{i:03d}")
        
        if cluster_coords:
            cluster_coords = np.array(cluster_coords)
            # 中心座標（重心）を計算
            center = cluster_coords.mean(axis=0)
            # クラスターの広がり（中心からの平均距離）を計算
            distances = np.linalg.norm(cluster_coords - center, axis=1)
            radius = float(distances.mean())
            
            clusters.append({
                "id": cluster_id,
                "keywords": keywords,
                "center": {
                    "x": float(center[0]),
                    "y": float(center[1]),
                    "z": float(center[2])
                },
                "radius": round(radius, 4),
                "member_count": len(cluster_coords),
                "member_ids": cluster_member_ids
            })
        else:
            # メンバーがいない場合（通常は発生しない）
            clusters.append({
                "id": cluster_id,
                "keywords": keywords,
                "center": {"x": 0, "y": 0, "z": 0},
                "radius": 0,
                "member_count": 0,
                "member_ids": []
            })
    
    # 最終JSON
    final_json = {
        "metadata": {
            "total_teachers": len(nodes),
            "total_clusters": len(clusters),
            "total_edges": len(edges),
            "embedding_dim": embeddings.shape[1],
            "generated_date": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
        },
        "nodes": nodes,
        "edges": edges,
        "clusters": clusters
    }
    
    # JSONファイルに保存
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(final_json, f, ensure_ascii=False, indent=2)
    
    print(f"✅ JSONファイルを保存しました: {output_path}")
    print(f"\n📊 統計情報:")
    print(f"   - ノード数: {len(nodes)}")
    print(f"   - エッジ数: {len(edges)}")
    print(f"   - クラスター数: {len(clusters)}")
    print(f"   - 埋め込み次元: {embeddings.shape[1]}")


def main():
    """
    メイン処理
    """
    print("=" * 60)
    print("🚀 教員ネットワーク可視化用JSONデータ生成")
    print("=" * 60)
    
    # 設定ファイルから設定を読み込み
    try:
        from config import (
            INPUT_JSONL, OUTPUT_JSON, BERT_MODEL,
            UMAP_N_NEIGHBORS, UMAP_MIN_DIST,
            DBSCAN_EPS, DBSCAN_MIN_SAMPLES,
            SIMILARITY_THRESHOLD, TOP_KEYWORDS_PER_CLUSTER
        )
        print("\n⚙️  設定ファイル (config.py) を読み込みました")
    except ImportError:
        # デフォルト値を使用
        INPUT_JSONL = 'yugo_faculty_merged_v2.jsonl'
        OUTPUT_JSON = 'faculty_network_v2.json'
        BERT_MODEL = 'sonoisa/sentence-bert-base-ja-mean-tokens-v2'
        UMAP_N_NEIGHBORS = 15
        UMAP_MIN_DIST = 0.1
        DBSCAN_EPS = 0.5
        DBSCAN_MIN_SAMPLES = 3
        SIMILARITY_THRESHOLD = 0.65
        TOP_KEYWORDS_PER_CLUSTER = 5
        print("\n⚙️  デフォルト設定を使用します")
    
    print(f"\n📋 使用する設定:")
    print(f"   入力ファイル: {INPUT_JSONL}")
    print(f"   出力ファイル: {OUTPUT_JSON}")
    print(f"   類似度閾値: {SIMILARITY_THRESHOLD}")
    print(f"   DBSCAN EPS: {DBSCAN_EPS}")
    print(f"   DBSCAN MIN_SAMPLES: {DBSCAN_MIN_SAMPLES}")
    
    # 1. データ読み込み
    faculty_data = load_faculty_data(INPUT_JSONL)
    
    # 2. BERT埋め込みベクトルの計算
    embeddings = compute_bert_embeddings(faculty_data, model_name=BERT_MODEL)
    
    # 3. 次元削減（3D）
    coords_3d = reduce_to_3d(embeddings, n_neighbors=UMAP_N_NEIGHBORS, min_dist=UMAP_MIN_DIST)
    
    # 4. クラスタリング
    cluster_labels = cluster_with_dbscan(coords_3d, eps=DBSCAN_EPS, min_samples=DBSCAN_MIN_SAMPLES)
    
    # 5. クラスターのキーワード分析
    cluster_keywords = extract_cluster_keywords(faculty_data, cluster_labels, top_n=TOP_KEYWORDS_PER_CLUSTER)
    
    # 6. エッジリストの作成
    edges = compute_similarity_edges(embeddings, faculty_data, threshold=SIMILARITY_THRESHOLD)
    
    # 7. 最終JSONの作成
    create_final_json(
        faculty_data, 
        embeddings, 
        coords_3d, 
        cluster_labels, 
        cluster_keywords, 
        edges,
        output_path=OUTPUT_JSON
    )
    
    print("\n" + "=" * 60)
    print("✨ すべての処理が完了しました！")
    print("=" * 60)


if __name__ == '__main__':
    main()
