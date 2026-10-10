"""
05 — Scrape Kanazawa University syllabus list pages
     → data/raw/syllabus/kanazawa_syllabus.csv (+ eng)

Requires Edge WebDriver (Selenium). Detail URLs must include lct_idx.

Usage:
  python scraping/05_scrape_syllabus_list.py
"""
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.edge.service import Service
from selenium.webdriver.support.ui import Select, WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException
from webdriver_manager.microsoft import EdgeChromiumDriverManager
import time
import re
import pandas as pd
import os
import sys
from pathlib import Path

# パス定義
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data" / "raw" / "syllabus"
DEBUG_DIR = Path(__file__).resolve().parent / "debugfile"
DEBUG_DIR.mkdir(parents=True, exist_ok=True)

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

# Edgeドライバーの設定
driver = get_edge_driver()

# シラバス検索ページを開く
URL = "https://eduweb.sta.kanazawa-u.ac.jp/portal/Public/Syllabus/SearchMain.aspx"

# URLを開く（タイムアウトとリトライ対応）
max_retries = 3
retry_delay = 5

for attempt in range(max_retries):
    try:
        print(f"\n→ ページを読み込み中... (試行 {attempt + 1}/{max_retries})")
        driver.set_page_load_timeout(30)  # ページロード タイムアウト: 30秒
        driver.get(URL)
        print("✓ ページ読み込み成功")
        break
    except Exception as e:
        error_msg = str(e)
        print(f"✗ ネットワークエラー: {error_msg[:100]}")
        
        if attempt < max_retries - 1:
            print(f"  {retry_delay}秒待機してリトライします...")
            time.sleep(retry_delay)
        else:
            print("\n❌ 大学サーバーに接続できません。")
            print("考えられる原因:")
            print("  1. 大学サーバーが一時的にダウンしている可能性")
            print("  2. ISP/ネットワークがそのサーバーへのアクセスを制限している可能性")
            print("  3. 自動アクセス制限により接続がブロックされている可能性\n")
            print("対策:")
            print("  ・ しばらく待機後に再度試してください")
            print("  ・ VPN を使用してアクセスを試みてください")
            print("  ・ スクリプトを非自動スクレイピング方式に変更してください\n")
            driver.quit()
            exit(1)

# ページが完全に読み込まれるのを待つ

try:
    WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.ID, "ctl00_Head1"))
        )
    # デバッグ用にHTMLを保存
    # with open(DEBUG_DIR / "main_debug_page.html", "w", encoding="utf-8") as f:
    #     f.write(driver.page_source)
except:
    print("ページが見つかりませんでした。")
    driver.quit()
    exit()


# 検索条件を設定
def select_dropdown(element_id, visible_text):
    dropdown = Select(driver.find_element(By.ID, element_id))
    dropdown.select_by_visible_text(visible_text)
    time.sleep(1)  # 変更を反映するために少し待機


def split_course_hierarchy(value):
    parts = [p.strip() for p in value.split("|", 2)]
    while len(parts) < 3:
        parts.append("")
    return parts[0], parts[1], parts[2]


def safe_select_by_value(element_id, value):
    try:
        dropdown = Select(driver.find_element(By.ID, element_id))
        values = [opt.get_attribute("value") for opt in dropdown.options]
        if value in values:
            dropdown.select_by_value(value)
            time.sleep(1)
            return True
    except Exception:
        return False
    return False


def safe_select_page_size_100():
    # 結果一覧に件数プルダウンがある場合は100件表示にする
    for candidate in ["100", "50", "30", "20", "10"]:
        if safe_select_by_value("ctl00_phContents_ucGrid_ddl", candidate):
            return candidate
    return None


DETAIL_URL_RE = re.compile(
    r"(https?://[^\s\"'<>]*DetailMain\.aspx\?[^\s\"'<>]*lct_idx=[A-Za-z0-9]+)",
    re.IGNORECASE,
)
LCT_PARAM_RE = re.compile(
    r"lct_year=(\d+).*?lct_cd=([^&\"'\s]+).*?je_cd=(\d+).*?lct_idx=([A-Za-z0-9]+)",
    re.IGNORECASE | re.DOTALL,
)


def extract_detail_link(cell):
    """セル内のリンク / onclick から DetailMain.aspx URL を完全取得（lct_idx 含む）。"""
    anchors = cell.find_elements(By.TAG_NAME, "a")
    for anchor in anchors:
        for attr in ("href", "data-href", "data-url"):
            raw = (anchor.get_attribute(attr) or "").strip()
            if "DetailMain.aspx" in raw and "lct_idx=" in raw:
                return raw.split("#")[0]
            m = DETAIL_URL_RE.search(raw)
            if m:
                return m.group(1)

        onclick = (anchor.get_attribute("onclick") or "") + " " + (anchor.get_attribute("href") or "")
        m = DETAIL_URL_RE.search(onclick)
        if m:
            return m.group(1)
        pm = LCT_PARAM_RE.search(onclick)
        if pm:
            year, cd, je, idx = pm.groups()
            return (
                "https://eduweb.sta.kanazawa-u.ac.jp/Portal/Public/Syllabus/DetailMain.aspx"
                f"?lct_year={year}&lct_cd={cd}&je_cd={je}&lct_idx={idx}"
            )
    # セル全体の HTML からも救済
    try:
        html = cell.get_attribute("innerHTML") or ""
        m = DETAIL_URL_RE.search(html)
        if m:
            return m.group(1)
        pm = LCT_PARAM_RE.search(html)
        if pm:
            year, cd, je, idx = pm.groups()
            return (
                "https://eduweb.sta.kanazawa-u.ac.jp/Portal/Public/Syllabus/DetailMain.aspx"
                f"?lct_year={year}&lct_cd={cd}&je_cd={je}&lct_idx={idx}"
            )
    except Exception:
        pass
    return "N/A"


def get_target_faculties():
    fac_dropdown = Select(driver.find_element(By.ID, "ctl00_phContents_ddl_fac"))
    targets = []
    for option in fac_dropdown.options:
        value = option.get_attribute("value")
        text = option.text.strip()
        if value and value != "NULL":
            targets.append((value, text))
    return targets


def click_language_button(lang="eng"):
    """言語切り替えボタンをクリック（eng=英語、jpn=日本語）"""
    if lang == "eng":
        button_id = "ctl00_bhHeader_slLanguage_imgBtnEng"
        lang_name = "英語"
    else:
        button_id = "ctl00_bhHeader_slLanguage_imgBtnJpn"
        lang_name = "日本語"
    
    for attempt in range(1, 4):
        try:
            button = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.ID, button_id))
            )

            # 既に対象言語の場合、ボタンが disabled になっている
            if button.get_attribute("disabled"):
                print(f"✓ 既に{lang_name}表示です")
                return True

            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
            time.sleep(0.3)
            try:
                button.click()
            except Exception:
                driver.execute_script("arguments[0].click();", button)

            # 切り替え直後に検索条件の年度ドロップダウンが見える状態まで待つ
            WebDriverWait(driver, 12).until(
                EC.presence_of_element_located((By.ID, "ctl00_phContents_ddl_year"))
            )
            print(f"✓ {lang_name}に切り替え成功")
            return True
        except Exception as e:
            if attempt < 3:
                print(f"⚠ {lang_name}切り替え再試行 {attempt}/2")
                # 画面状態を戻して再試行
                ensure_search_page()
                time.sleep(1)
                continue
            print(f"✗ {lang_name}への切り替え失敗: {e}")
            return False


def select_year_2026():
    """表示言語に依存せず 2026 年度を選択する"""
    try:
        dropdown = Select(driver.find_element(By.ID, "ctl00_phContents_ddl_year"))
        for option in dropdown.options:
            text = option.text.strip()
            if "2026" in text:
                dropdown.select_by_visible_text(text)
                time.sleep(1)
                return True
    except Exception:
        return False
    return False


def ensure_search_page():
    """現在が検索結果画面なら検索画面へ戻す"""
    if driver.find_elements(By.ID, "ctl00_phContents_ctl06_btnSearch"):
        return True

    # 「検索画面に戻る」リンクから戻る
    return_candidates = [
        (By.ID, "ctl00_phContents_lnkReturn_Up_lnk"),
        (By.ID, "ctl00_phContents_lnkReturn_Down_lnk"),
        (By.XPATH, "//a[contains(normalize-space(text()),'検索画面に戻る') or contains(normalize-space(text()),'Back to Search')]")
    ]
    for by, locator in return_candidates:
        links = driver.find_elements(by, locator)
        if links:
            driver.execute_script("arguments[0].click();", links[0])
            try:
                WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.ID, "ctl00_phContents_ctl06_btnSearch"))
                )
                return True
            except Exception:
                pass

    # 念のため検索トップへ再アクセス
    try:
        driver.get(URL)
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.ID, "ctl00_phContents_ctl06_btnSearch"))
        )
        return True
    except Exception:
        return False


select_year_2026()  # 開講年度固定

# ===== 日本語版のデータ取得 =====
print("=== 日本語版データ取得開始 ===\n")
all_data = []
seen_keys = set()

faculties = get_target_faculties()
print(f"対象の開講学域・研究科数: {len(faculties)}")

for idx, (fac_value, fac_name) in enumerate(faculties, start=1):
    print(f"\n[{idx}/{len(faculties)}] 学域・研究科: {fac_name}")

    if not ensure_search_page():
        print("  -> 検索画面への復帰に失敗。次の学域へスキップ")
        continue
    select_year_2026()

    # 学域・研究科を選択すると学類・開講学期が再読み込みされるため、毎回リセットする
    safe_select_by_value("ctl00_phContents_ddl_fac", fac_value)
    safe_select_by_value("ctl00_phContents_ddl_dpt", "NULL")
    safe_select_by_value("ctl00_phContents_ddl_term", "NULL")

    search_button = driver.find_element(By.ID, "ctl00_phContents_ctl06_btnSearch")
    search_button.click()

    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.ID, "ctl00_phContents_ucGrid_grv"))
        )
    except Exception:
        print(f"  -> {fac_name} は検索結果なし")
        continue

    page_size = safe_select_page_size_100()
    if page_size:
        print(f"  -> 一覧表示件数: {page_size}件")

    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    time.sleep(1)

    try:
        page_no = 1
        while True:
            table = WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.ID, "ctl00_phContents_ucGrid_grv"))
            )
            rows = table.find_elements(By.TAG_NAME, "tr")
            print(f"  -> ページ{page_no} 行数(ヘッダー含む): {len(rows)}")

            if len(rows) > 1:
                for row in rows[1:]:
                    cols = row.find_elements(By.TAG_NAME, "td")
                    if len(cols) < 8:
                        continue

                    no = cols[0].text.strip()
                    subject_code = cols[1].text.strip()
                    jap_link = extract_detail_link(cols[2])
                    eng_link = extract_detail_link(cols[3])

                    # ページャ行（javascript:__doPostBack など）を除外
                    if jap_link == "N/A" and eng_link == "N/A":
                        continue
                    if not subject_code:
                        continue

                    faculty_subject = cols[4].text.strip().replace("\n", " | ")
                    faculty, department, subject_name = split_course_hierarchy(faculty_subject)
                    teacher = cols[5].text.strip()
                    semester = cols[6].text.strip()
                    schedule = cols[7].text.strip()

                    record_key = (subject_code, jap_link)
                    if record_key in seen_keys:
                        continue
                    seen_keys.add(record_key)

                    all_data.append([
                        no,
                        subject_code,
                        jap_link,
                        eng_link,
                        faculty,
                        department,
                        subject_name,
                        teacher,
                        semester,
                        schedule,
                    ])

            # ASP.NET のページャは「次へ」が出ないことがあるため、Page$<次ページ番号>で遷移する
            next_links = driver.find_elements(By.XPATH, f"//a[contains(@href,'Page${page_no + 1}')]")
            if not next_links:
                break

            time.sleep(1)
            driver.execute_script("arguments[0].click();", next_links[0])
            WebDriverWait(driver, 20).until(EC.staleness_of(table))
            page_no += 1

        with open(DEBUG_DIR / "result_debug_page.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)

    except NoSuchElementException:
        print(f"  -> {fac_name} の取得中にエラー")


df = pd.DataFrame(
    all_data,
    columns=[
        "No.",
        "時間割コード",
        "日URL",
        "英URL",
        "開講学域",
        "開講学類",
        "授業科目",
        "担当教員",
        "開講期",
        "曜日・時限",
    ],
)

df.to_csv(DATA_DIR / "kanazawa_syllabus.csv", index=False, encoding="utf-8-sig")
print(f"\n日本語版保存完了: {len(df)}件")

# ===== 英語版のデータ取得 =====
print("\n\n=== 英語版データ取得開始 ===\n")
if ensure_search_page() and click_language_button("eng"):
    # 年度を再度設定（言語切り替え後にリセットされる可能性あり）
    if not select_year_2026():
        print("⚠ 年度再設定に失敗（英語表示で候補名が変わっている可能性）")
    
    all_data_eng = []
    seen_keys_eng = set()
    
    faculties = get_target_faculties()
    print(f"対象の開講学域・研究科数: {len(faculties)}")
    
    for idx, (fac_value, fac_name) in enumerate(faculties, start=1):
        print(f"\n[{idx}/{len(faculties)}] Faculty/Department: {fac_name}")

        if not ensure_search_page():
            print("  -> Failed to return to search page. Skip this faculty.")
            continue
        select_year_2026()
    
        # 学域・研究科を選択
        safe_select_by_value("ctl00_phContents_ddl_fac", fac_value)
        safe_select_by_value("ctl00_phContents_ddl_dpt", "NULL")
        safe_select_by_value("ctl00_phContents_ddl_term", "NULL")
    
        search_button = driver.find_element(By.ID, "ctl00_phContents_ctl06_btnSearch")
        search_button.click()
    
        try:
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.ID, "ctl00_phContents_ucGrid_grv"))
            )
        except Exception:
            print(f"  -> No search results for {fac_name}")
            continue
    
        page_size = safe_select_page_size_100()
        if page_size:
            print(f"  -> Results per page: {page_size}")
    
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(1)
    
        try:
            page_no = 1
            while True:
                table = WebDriverWait(driver, 20).until(
                    EC.presence_of_element_located((By.ID, "ctl00_phContents_ucGrid_grv"))
                )
                rows = table.find_elements(By.TAG_NAME, "tr")
                print(f"  -> Page {page_no} rows (including header): {len(rows)}")
    
                if len(rows) > 1:
                    for row in rows[1:]:
                        cols = row.find_elements(By.TAG_NAME, "td")
                        if len(cols) < 8:
                            continue
    
                        no = cols[0].text.strip()
                        subject_code = cols[1].text.strip()
                        jap_link = extract_detail_link(cols[2])
                        eng_link = extract_detail_link(cols[3])

                        # ページャ行（javascript:__doPostBack など）を除外
                        if jap_link == "N/A" and eng_link == "N/A":
                            continue
                        if not subject_code:
                            continue

                        faculty_subject = cols[4].text.strip().replace("\n", " | ")
                        faculty, department, subject_name = split_course_hierarchy(faculty_subject)
                        teacher = cols[5].text.strip()
                        semester = cols[6].text.strip()
                        schedule = cols[7].text.strip()
    
                        record_key = (subject_code, jap_link)
                        if record_key in seen_keys_eng:
                            continue
                        seen_keys_eng.add(record_key)
    
                        all_data_eng.append([
                            no,
                            subject_code,
                            jap_link,
                            eng_link,
                            faculty,
                            department,
                            subject_name,
                            teacher,
                            semester,
                            schedule,
                        ])
    
                next_links = driver.find_elements(By.XPATH, f"//a[contains(@href,'Page${page_no + 1}')]")
                if not next_links:
                    break
    
                time.sleep(1)
                driver.execute_script("arguments[0].click();", next_links[0])
                WebDriverWait(driver, 20).until(EC.staleness_of(table))
                page_no += 1
    
            with open(DEBUG_DIR / "result_debug_page_eng.html", "w", encoding="utf-8") as f:
                f.write(driver.page_source)
    
        except NoSuchElementException:
            print(f"  -> Error retrieving {fac_name}")
    
    df_eng = pd.DataFrame(
        all_data_eng,
        columns=[
            "No.",
            "Subject Code",
            "Japanese URL",
            "English URL",
            "Faculty",
            "Department",
            "Subject Name",
            "Instructor",
            "Term",
            "Day/Period",
        ],
    )
    
    df_eng.to_csv(DATA_DIR / "kanazawa_syllabus_eng.csv", index=False, encoding="utf-8-sig")
    print(f"\n英語版保存完了: {len(df_eng)}件")
else:
    print("⚠ 英語版への切り替えに失敗したため、列名のみ英語化したフォールバックCSVを出力します")
    df_eng = pd.DataFrame(
        all_data,
        columns=[
            "No.",
            "Subject Code",
            "Japanese URL",
            "English URL",
            "Faculty",
            "Department",
            "Subject Name",
            "Instructor",
            "Term",
            "Day/Period",
        ],
    )
    df_eng.to_csv(DATA_DIR / "kanazawa_syllabus_eng.csv", index=False, encoding="utf-8-sig")
    print(f"\n英語版フォールバック保存完了: {len(df_eng)}件")

driver.quit()
