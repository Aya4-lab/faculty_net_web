from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from bs4 import BeautifulSoup
import time
import pandas as pd
from urllib.parse import urljoin
from webdriver_manager.microsoft import EdgeChromiumDriverManager
import os
import sys
from pathlib import Path

# パス定義
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data" / "raw" / "syllabus"

# ドライバー取得関数（フォールバック対応）
def get_edge_driver():
    """
    System Edge ブラウザを使用。ドライバーは自動検出。
    WebDriver Manager がネットワークに接続できない場合に対応。
    軽量化: システムEdgeを最初に試す
    """
    options = webdriver.EdgeOptions()
    # options.add_argument("--headless")  # ブラウザを表示する（デバッグ用）
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    
    try:
        # 方法1: システム Edge ブラウザを直接使用（ドライバー自動検出）← 軽量（即座）
        try:
            print("→ システムの Edge ブラウザで接続中...")
            driver = webdriver.Edge(options=options)
            print("✓ システム Edge 接続成功（軽量モード）")
            return driver
        except Exception as e:
            if "No such file" in str(e) or "msedgedriver" in str(e):
                print("⚠ 警告: システム Edge WebDriver が見つかりません")
                print("  フォールバック: webdriver-manager から自動ダウンロード...\n")
            else:
                raise
        
        # 方法2: webdriver-manager で自動ダウンロード（ネットワークが必要）← フォールバック
        try:
            service = Service(EdgeChromiumDriverManager().install())
            driver = webdriver.Edge(service=service, options=options)
            print("✓ 自動ドライバーダウンロード成功")
            return driver
        except Exception as e:
            if "Could not reach host" in str(e) or "getaddrinfo failed" in str(e):
                print("✗ ネットワークエラー: msedgedriver.azureedge.net に接続できません")
                raise
        
    except Exception as e:
        print(f"\n❌ エラー: Edge ドライバーを起動できません")
        print(f"詳細: {e}\n")
        print("解決策:")
        print("1. ネットワーク接続を確認してください")
        print("2. または、Microsoft Edge WebDriver を手動ダウンロード:")
        print("   https://developer.microsoft.com/en-us/microsoft-edge/tools/webdriver/")
        print("3. ダウンロードしたドライバーを network/ フォルダに置いてください\n")
        sys.exit(1)

# Seleniumの設定
driver = get_edge_driver()

# kanazawa_syllabus.csv を読み込む
syllabus_data = pd.read_csv(DATA_DIR / "kanazawa_syllabus.csv")

# 英語URL専用CSV（存在すれば優先利用）
syllabus_data_eng = None
try:
    syllabus_data_eng = pd.read_csv(DATA_DIR / "kanazawa_syllabus_eng.csv")
    print(f"English URL source loaded: kanazawa_syllabus_eng.csv ({len(syllabus_data_eng)} rows)")
except Exception as e:
    print(f"⚠ Could not load kanazawa_syllabus_eng.csv: {e}")
    print("  Fallback: 英語URLは kanazawa_syllabus.csv 側の列を使用")

# 全ての基本情報とスケジュール情報を格納するリスト
all_basic_info = []
all_schedule = []
all_rubric = []
all_attribute = []

# 英語版用のリスト
all_basic_info_eng = []
all_schedule_eng = []
all_rubric_eng = []
all_attribute_eng = []

# 中間保存間隔
AUTOSAVE_INTERVAL = 1000

# ページ要素待機秒数
WAIT_TIMEOUT = 20

# セル内改行の置換文字
LINE_BREAK_REPLACEMENT = " | "


def flatten_text(text):
    """セル内の改行を区切り文字に置換して1行化する。"""
    if text is None:
        return ""
    return str(text).replace("\r\n", "\n").replace("\r", "\n").replace("\n", LINE_BREAK_REPLACEMENT).strip()


def wait_for_detail_page():
    """詳細ページの主キー要素が見えるまで待機する。"""
    try:
        WebDriverWait(driver, WAIT_TIMEOUT).until(
            EC.presence_of_element_located((By.ID, "ctl00_phContents_Detail_lbl_lct_cd"))
        )
        return True
    except TimeoutException:
        return False


def append_records_to_csv(records, filename):
    """レコードをCSVへ追記する（初回のみヘッダー付き）。"""
    if not records:
        return

    csv_path = DATA_DIR / filename
    file_exists = csv_path.exists() and csv_path.stat().st_size > 0
    encoding = "utf-8" if file_exists else "utf-8-sig"
    pd.DataFrame(records).to_csv(
        csv_path,
        mode="a",
        index=False,
        header=not file_exists,
        encoding=encoding,
    )


def initialize_output_files():
    """毎回の実行開始時に出力ファイルを初期化する。"""
    output_files = [
        "all_basic_info.csv",
        "all_schedule.csv",
        "all_rubric.csv",
        "all_attribute.csv",
        "all_basic_info_eng.csv",
        "all_schedule_eng.csv",
        "all_rubric_eng.csv",
        "all_attribute_eng.csv",
    ]
    for filename in output_files:
        csv_path = DATA_DIR / filename
        if csv_path.exists():
            csv_path.unlink()


initialize_output_files()

# 前回保存済み位置（追記式）
jpn_saved_idx = {"basic": 0, "schedule": 0, "rubric": 0, "attribute": 0}
eng_saved_idx = {"basic": 0, "schedule": 0, "rubric": 0, "attribute": 0}


def save_japanese_outputs():
    """日本語版CSVを差分追記保存する。"""
    global jpn_saved_idx
    append_records_to_csv(all_basic_info[jpn_saved_idx["basic"] :], "all_basic_info.csv")
    append_records_to_csv(all_schedule[jpn_saved_idx["schedule"] :], "all_schedule.csv")
    append_records_to_csv(all_rubric[jpn_saved_idx["rubric"] :], "all_rubric.csv")
    append_records_to_csv(all_attribute[jpn_saved_idx["attribute"] :], "all_attribute.csv")
    jpn_saved_idx = {
        "basic": len(all_basic_info),
        "schedule": len(all_schedule),
        "rubric": len(all_rubric),
        "attribute": len(all_attribute),
    }


def save_english_outputs():
    """英語版CSVを差分追記保存する。"""
    global eng_saved_idx
    append_records_to_csv(all_basic_info_eng[eng_saved_idx["basic"] :], "all_basic_info_eng.csv")
    append_records_to_csv(all_schedule_eng[eng_saved_idx["schedule"] :], "all_schedule_eng.csv")
    append_records_to_csv(all_rubric_eng[eng_saved_idx["rubric"] :], "all_rubric_eng.csv")
    append_records_to_csv(all_attribute_eng[eng_saved_idx["attribute"] :], "all_attribute_eng.csv")
    eng_saved_idx = {
        "basic": len(all_basic_info_eng),
        "schedule": len(all_schedule_eng),
        "rubric": len(all_rubric_eng),
        "attribute": len(all_attribute_eng),
    }


def get_english_url(index, fallback_row):
    """英語URLを取得する。優先: kanazawa_syllabus_eng.csv の English URL。"""
    if syllabus_data_eng is not None and index < len(syllabus_data_eng):
        eng_row = syllabus_data_eng.iloc[index]
        for col in ["English URL", "英URL"]:
            if col in eng_row and pd.notna(eng_row[col]) and str(eng_row[col]).strip():
                return str(eng_row[col]).strip()

    for col in ["英URL", "English URL"]:
        if col in fallback_row and pd.notna(fallback_row[col]) and str(fallback_row[col]).strip():
            return str(fallback_row[col]).strip()

    return ""


# ===== 日本語版のデータ取得 =====
print("=== Japanese Version Data Extraction ===\n")
for index, row in syllabus_data.iterrows():
    try:
        # シラバス詳細ページのURLを取得
        url = row["日URL"]
        print(f"処理中のURL: {url}")
        
        # ページ読み込み（リトライ対応）
        try:
            driver.set_page_load_timeout(30)
            driver.get(url)
        except Exception as e:
            if "Connection" in str(e) or "timeout" in str(e).lower():
                print(f"  ⚠ 接続エラーをスキップ: {str(e)[:60]}")
                continue  # 次のURLへ
            else:
                raise

        # 要素が取得できたら次へ
        if not wait_for_detail_page():
            print(f"  ⚠ 詳細要素の取得に失敗: {url}")
            continue

        # BeautifulSoupで解析
        soup = BeautifulSoup(driver.page_source, "html.parser")

        # データを取得する関数
        def get_text_or_default(soup, element_id, default="データなし"):
            element = soup.find(id=element_id)
            if element:
                text = element.get_text(separator=LINE_BREAK_REPLACEMENT, strip=True)
                return flatten_text(text)
            return default

        # 基本情報を取得
        subject_name = get_text_or_default(soup, "ctl00_phContents_Detail_lbl_lct_name_double")
        teacher = get_text_or_default(soup, "ctl00_phContents_Detail_lbl_syl_staff_name_link_double")
        subject_number = get_text_or_default(soup, "ctl00_phContents_Detail_lbl_numbering")
        timetable_number = get_text_or_default(soup, "ctl00_phContents_Detail_lbl_lct_cd")
        subject_division = get_text_or_default(soup, "ctl00_phContents_Detail_lbl_sbj_div_name")
        lecture_type = get_text_or_default(soup, "ctl00_phContents_Detail_lbl_lct_type_name")
        faculty_name = get_text_or_default(soup, "ctl00_phContents_Detail_lbl_faculty_name")
        class_size = get_text_or_default(soup, "ctl00_phContents_Detail_lbl_class_size_disp")
        term_name = get_text_or_default(soup, "ctl00_phContents_Detail_lbl_lct_term_name")
        day_period = get_text_or_default(soup, "ctl00_phContents_Detail_lbl_day_period")
        credits = get_text_or_default(soup, "ctl00_phContents_Detail_lbl_credits_disp")
        class_format = get_text_or_default(soup, "ctl00_phContents_Detail_lbl_format_name_disp")
        credit_limit_60 = get_text_or_default(soup, "ctl00_phContents_Detail_lbl_media_name_disp")
        target_students = get_text_or_default(soup, "ctl00_phContents_Detail_lbl_target")
        keywords = get_text_or_default(soup, "ctl00_phContents_Detail_lbl_keyword_disp")
        lecture_room = get_text_or_default(soup, "ctl00_phContents_Detail_lbl_lecture_room_infomation")
        open_course = get_text_or_default(soup, "ctl00_phContents_Detail_lbl_open_account")
        remarks = get_text_or_default(soup, "ctl00_phContents_Detail_lbl_note")
        grading_method = get_text_or_default(soup, "ctl00_phContents_Detail_txt_grading_method_referTxt")
        grading_rate = get_text_or_default(soup, "ctl00_phContents_Detail_txt_grading_rate_referTxt")
        pre_learning = get_text_or_default(soup, "ctl00_phContents_Detail_txt_pre_learning_referTxt")
        pre_materials = get_text_or_default(soup, "ctl00_phContents_Detail_tbl_pre_materials_referTxt")
        post_learning = get_text_or_default(soup, "ctl00_phContents_Detail_txt_post_learning_referTxt")
        post_materials = get_text_or_default(soup, "ctl00_phContents_Detail_tbl_post_materials_referTxt")
        office_hours = get_text_or_default(soup, "ctl00_phContents_Detail_txt_office_hours_referTxt")
        topic = get_text_or_default(soup, "ctl00_phContents_Detail_txt_topic_referTxt")
        objectives = get_text_or_default(soup, "ctl00_phContents_Detail_txt_objective_referTxt")
        outline = get_text_or_default(soup, "ctl00_phContents_Detail_txt_outline_referTxt")
        textbooks = get_text_or_default(soup, "ctl00_phContents_Detail_txt_books_note_referTxt")

        # 教材ファイル名とリンクを保持
        file_upload_table = soup.find(id="ctl00_phContents_Detail_lblFileUpload_gv")
        file_items = []
        if file_upload_table:
            links = file_upload_table.find_all("a")
            for link in links:
                file_name = flatten_text(link.get_text(separator=LINE_BREAK_REPLACEMENT, strip=True))
                file_url = link.get("href", "")
                if file_url:
                    file_url = urljoin(url, file_url)
                if file_name:
                    file_items.append(f"{file_name} ({file_url})" if file_url else file_name)
        file_uploads = " | ".join(file_items) if file_items else "データなし"

        # 基本情報をリストに追加
        all_basic_info.append({
            "科目名": subject_name,
            "担当教員": teacher,
            "科目ナンバー": subject_number,
            "時間割番号": timetable_number,
            "科目区分": subject_division,
            "講義形態": lecture_type,
            "開講学域等": faculty_name,
            "適正人数": class_size,
            "開講学期": term_name,
            "曜日・時限": day_period,
            "単位数": credits,
            "授業形態": class_format,
            "60単位上限": credit_limit_60,
            "対象学生": target_students,
            "キーワード": keywords,
            "講義室情報": lecture_room,
            "開放科目": open_course,
            "備考": remarks,
            "評価方法": grading_method,
            "評価の割合": grading_rate,
            "予習に関する指示": pre_learning,
            "予習に関する教材": pre_materials,
            "復習に関する指示": post_learning,
            "復習に関する教材": post_materials,
            "オフィスアワー等": office_hours,
            "教材ファイル": file_uploads,
            "授業の主題": topic,
            "学習目標": objectives,
            "授業概要": outline,
            "教科書・参考書": textbooks
        })

        # ルーブリック表を行構造のまま保持
        rubric_table = soup.find(id="ctl00_phContents_Detail_cateRubrics_gvRefer")
        if rubric_table:
            rubric_rows = rubric_table.find_all("tr")
            for rubric_row in rubric_rows[2:]:  # ヘッダー2行をスキップ
                cols = rubric_row.find_all("td")
                if len(cols) >= 4:
                    all_rubric.append({
                        "科目名": subject_name,
                        "時間割番号": timetable_number,
                        "評価項目": flatten_text(cols[0].get_text(separator=LINE_BREAK_REPLACEMENT, strip=True)),
                        "S～A": flatten_text(cols[1].get_text(separator=LINE_BREAK_REPLACEMENT, strip=True)),
                        "B～C": flatten_text(cols[2].get_text(separator=LINE_BREAK_REPLACEMENT, strip=True)),
                        "不可": flatten_text(cols[3].get_text(separator=LINE_BREAK_REPLACEMENT, strip=True))
                    })

        # 授業に関する属性を行構造のまま保持
        attribute_table = soup.find(id="ctl00_phContents_Detail_ucSylAtrb_gvRefer")
        if attribute_table:
            attribute_rows = attribute_table.find_all("tr")
            for attribute_row in attribute_rows[1:]:  # ヘッダー行をスキップ
                cols = attribute_row.find_all("td")
                if len(cols) >= 4:
                    all_attribute.append({
                        "科目名": subject_name,
                        "時間割番号": timetable_number,
                        "No": flatten_text(cols[0].get_text(separator=LINE_BREAK_REPLACEMENT, strip=True)),
                        "属性": flatten_text(cols[1].get_text(separator=LINE_BREAK_REPLACEMENT, strip=True)),
                        "選択項目": flatten_text(cols[2].get_text(separator=LINE_BREAK_REPLACEMENT, strip=True)),
                        "備考": flatten_text(cols[3].get_text(separator=LINE_BREAK_REPLACEMENT, strip=True))
                    })

        # 講義スケジュールを取得
        schedule_table = soup.find(id="ctl00_phContents_Detail_ucLctSchedule_gvRefer")
        if schedule_table:
            rows = schedule_table.find_all("tr")[1:]  # ヘッダー行をスキップ
            for row in rows:
                cols = row.find_all("td")
                lecture_number = flatten_text(cols[0].get_text(separator=LINE_BREAK_REPLACEMENT, strip=True)) if len(cols) > 0 else "データなし"
                theme = flatten_text(cols[1].get_text(separator=LINE_BREAK_REPLACEMENT, strip=True)) if len(cols) > 1 else "データなし"
                details = flatten_text(cols[2].get_text(separator=LINE_BREAK_REPLACEMENT, strip=True)) if len(cols) > 2 else "データなし"
                instructor = flatten_text(cols[3].get_text(separator=LINE_BREAK_REPLACEMENT, strip=True)) if len(cols) > 3 else "データなし"
                all_schedule.append({
                    "科目名": subject_name,  
                    "時間割番号": timetable_number,  
                    "講義回": lecture_number,
                    "テーマ": theme,
                    "具体的な内容": details,
                    "担当教員": instructor
                })

    except Exception as e:
        print(f"エラーが発生しました: {e}")

    if (index + 1) % AUTOSAVE_INTERVAL == 0:
        save_japanese_outputs()
        print(f"[JPN] 中間保存: {index + 1}件処理")

save_japanese_outputs()
print("Japanese version saved: all_basic_info.csv, all_schedule.csv, all_rubric.csv, all_attribute.csv")

# ===== 英語版のデータ取得 =====
print("\n\n=== English Version Data Extraction ===\n")
for index, row in syllabus_data.iterrows():
    try:
        # 英語URLを直接利用（言語切り替えなし）
        url = get_english_url(index, row)
        if not url:
            print(f"  ⚠ English URL missing, skipped: row={index + 1}")
            continue
        print(f"Processing URL (English): {url}")

        # ページ読み込み（リトライ対応）
        try:
            driver.set_page_load_timeout(30)
            driver.get(url)
        except Exception as e:
            if "Connection" in str(e) or "timeout" in str(e).lower():
                print(f"  ⚠ Connection error skipped: {str(e)[:60]}")
                continue  # 次のURLへ
            else:
                raise
    
        # 要素が取得できたら次へ
        if not wait_for_detail_page():
            print(f"  ⚠ Detail element not found: {url}")
            continue
    
        # BeautifulSoupで解析
        soup = BeautifulSoup(driver.page_source, "html.parser")
    
        # データを取得する関数
        def get_text_or_default_eng(soup, element_id, default="No data"):
            element = soup.find(id=element_id)
            if element:
                text = element.get_text(separator=LINE_BREAK_REPLACEMENT, strip=True)
                return flatten_text(text)
            return default
    
        # 基本情報を取得（英語版）
        subject_name = get_text_or_default_eng(soup, "ctl00_phContents_Detail_lbl_lct_name_double")
        teacher = get_text_or_default_eng(soup, "ctl00_phContents_Detail_lbl_syl_staff_name_link_double")
        subject_number = get_text_or_default_eng(soup, "ctl00_phContents_Detail_lbl_numbering")
        timetable_number = get_text_or_default_eng(soup, "ctl00_phContents_Detail_lbl_lct_cd")
        subject_division = get_text_or_default_eng(soup, "ctl00_phContents_Detail_lbl_sbj_div_name")
        lecture_type = get_text_or_default_eng(soup, "ctl00_phContents_Detail_lbl_lct_type_name")
        faculty_name = get_text_or_default_eng(soup, "ctl00_phContents_Detail_lbl_faculty_name")
        class_size = get_text_or_default_eng(soup, "ctl00_phContents_Detail_lbl_class_size_disp")
        term_name = get_text_or_default_eng(soup, "ctl00_phContents_Detail_lbl_lct_term_name")
        day_period = get_text_or_default_eng(soup, "ctl00_phContents_Detail_lbl_day_period")
        credits = get_text_or_default_eng(soup, "ctl00_phContents_Detail_lbl_credits_disp")
        class_format = get_text_or_default_eng(soup, "ctl00_phContents_Detail_lbl_format_name_disp")
        credit_limit_60 = get_text_or_default_eng(soup, "ctl00_phContents_Detail_lbl_media_name_disp")
        target_students = get_text_or_default_eng(soup, "ctl00_phContents_Detail_lbl_target")
        keywords = get_text_or_default_eng(soup, "ctl00_phContents_Detail_lbl_keyword_disp")
        lecture_room = get_text_or_default_eng(soup, "ctl00_phContents_Detail_lbl_lecture_room_infomation")
        open_course = get_text_or_default_eng(soup, "ctl00_phContents_Detail_lbl_open_account")
        remarks = get_text_or_default_eng(soup, "ctl00_phContents_Detail_lbl_note")
        grading_method = get_text_or_default_eng(soup, "ctl00_phContents_Detail_txt_grading_method_referTxt")
        grading_rate = get_text_or_default_eng(soup, "ctl00_phContents_Detail_txt_grading_rate_referTxt")
        pre_learning = get_text_or_default_eng(soup, "ctl00_phContents_Detail_txt_pre_learning_referTxt")
        pre_materials = get_text_or_default_eng(soup, "ctl00_phContents_Detail_tbl_pre_materials_referTxt")
        post_learning = get_text_or_default_eng(soup, "ctl00_phContents_Detail_txt_post_learning_referTxt")
        post_materials = get_text_or_default_eng(soup, "ctl00_phContents_Detail_tbl_post_materials_referTxt")
        office_hours = get_text_or_default_eng(soup, "ctl00_phContents_Detail_txt_office_hours_referTxt")
        topic = get_text_or_default_eng(soup, "ctl00_phContents_Detail_txt_topic_referTxt")
        objectives = get_text_or_default_eng(soup, "ctl00_phContents_Detail_txt_objective_referTxt")
        outline = get_text_or_default_eng(soup, "ctl00_phContents_Detail_txt_outline_referTxt")
        textbooks = get_text_or_default_eng(soup, "ctl00_phContents_Detail_txt_books_note_referTxt")
    
        # 教材ファイル名とリンクを保持
        file_upload_table = soup.find(id="ctl00_phContents_Detail_lblFileUpload_gv")
        file_items = []
        if file_upload_table:
            links = file_upload_table.find_all("a")
            for link in links:
                file_name = flatten_text(link.get_text(separator=LINE_BREAK_REPLACEMENT, strip=True))
                file_url = link.get("href", "")
                if file_url:
                    file_url = urljoin(url, file_url)
                if file_name:
                    file_items.append(f"{file_name} ({file_url})" if file_url else file_name)
        file_uploads = " | ".join(file_items) if file_items else "No data"
    
        # 基本情報をリストに追加（英語版）
        all_basic_info_eng.append({
                "Subject Name": subject_name,
                "Instructor": teacher,
                "Subject Number": subject_number,
                "Timetable Number": timetable_number,
                "Subject Division": subject_division,
                "Lecture Type": lecture_type,
                "Faculty": faculty_name,
                "Class Size": class_size,
                "Term": term_name,
                "Day/Period": day_period,
                "Credits": credits,
                "Class Format": class_format,
                "60 Credit Limit": credit_limit_60,
                "Target Students": target_students,
                "Keywords": keywords,
                "Lecture Room": lecture_room,
                "Open Course": open_course,
                "Remarks": remarks,
                "Grading Method": grading_method,
                "Grading Ratio": grading_rate,
                "Pre-learning Instructions": pre_learning,
                "Pre-learning Materials": pre_materials,
                "Post-learning Instructions": post_learning,
                "Post-learning Materials": post_materials,
                "Office Hours": office_hours,
                "Material Files": file_uploads,
                "Course Topic": topic,
                "Learning Objectives": objectives,
                "Course Outline": outline,
                "Textbooks/References": textbooks
        })
    
        # ルーブリック表を行構造のまま保持（英語版）
        rubric_table = soup.find(id="ctl00_phContents_Detail_cateRubrics_gvRefer")
        if rubric_table:
            rubric_rows = rubric_table.find_all("tr")
            for rubric_row in rubric_rows[2:]:
                cols = rubric_row.find_all("td")
                if len(cols) >= 4:
                    all_rubric_eng.append({
                            "Subject Name": subject_name,
                            "Timetable Number": timetable_number,
                            "Evaluation Item": flatten_text(cols[0].get_text(separator=LINE_BREAK_REPLACEMENT, strip=True)),
                            "S～A": flatten_text(cols[1].get_text(separator=LINE_BREAK_REPLACEMENT, strip=True)),
                            "B～C": flatten_text(cols[2].get_text(separator=LINE_BREAK_REPLACEMENT, strip=True)),
                            "Fail": flatten_text(cols[3].get_text(separator=LINE_BREAK_REPLACEMENT, strip=True))
                    })
    
        # 授業に関する属性を行構造のまま保持（英語版）
        attribute_table = soup.find(id="ctl00_phContents_Detail_ucSylAtrb_gvRefer")
        if attribute_table:
            attribute_rows = attribute_table.find_all("tr")
            for attribute_row in attribute_rows[1:]:
                cols = attribute_row.find_all("td")
                if len(cols) >= 4:
                    all_attribute_eng.append({
                            "Subject Name": subject_name,
                            "Timetable Number": timetable_number,
                            "No": flatten_text(cols[0].get_text(separator=LINE_BREAK_REPLACEMENT, strip=True)),
                            "Attribute": flatten_text(cols[1].get_text(separator=LINE_BREAK_REPLACEMENT, strip=True)),
                            "Option": flatten_text(cols[2].get_text(separator=LINE_BREAK_REPLACEMENT, strip=True)),
                            "Remarks": flatten_text(cols[3].get_text(separator=LINE_BREAK_REPLACEMENT, strip=True))
                    })
    
        # 講義スケジュールを取得（英語版）
        schedule_table = soup.find(id="ctl00_phContents_Detail_ucLctSchedule_gvRefer")
        if schedule_table:
            rows = schedule_table.find_all("tr")[1:]
            for row in rows:
                cols = row.find_all("td")
                lecture_number = flatten_text(cols[0].get_text(separator=LINE_BREAK_REPLACEMENT, strip=True)) if len(cols) > 0 else "No data"
                theme = flatten_text(cols[1].get_text(separator=LINE_BREAK_REPLACEMENT, strip=True)) if len(cols) > 1 else "No data"
                details = flatten_text(cols[2].get_text(separator=LINE_BREAK_REPLACEMENT, strip=True)) if len(cols) > 2 else "No data"
                instructor = flatten_text(cols[3].get_text(separator=LINE_BREAK_REPLACEMENT, strip=True)) if len(cols) > 3 else "No data"
                all_schedule_eng.append({
                        "Subject Name": subject_name,  
                        "Timetable Number": timetable_number,  
                        "Lecture No": lecture_number,
                        "Theme": theme,
                        "Details": details,
                        "Instructor": instructor
                })

    except Exception as e:
        print(f"Error occurred: {e}")

    if (index + 1) % AUTOSAVE_INTERVAL == 0:
        save_english_outputs()
        print(f"[ENG] Autosaved after {index + 1} records")

# ブラウザを閉じる
driver.quit()

# ===== 英語版の保存処理 =====
if all_basic_info_eng:
    save_english_outputs()
    print("English version saved: all_basic_info_eng.csv, all_schedule_eng.csv, all_rubric_eng.csv, all_attribute_eng.csv")
else:
    print("⚠ No English version data was extracted.")