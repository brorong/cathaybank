import time
from io import StringIO
import pandas as pd
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import TimeoutException
import gspread
import sqlite3


def fetch_all_cathay_funds():
    url = 'https://fund.cathaylife.com.tw/content.html?sUrl=$W$HTML$SELECT]DJHTM'

    options = webdriver.ChromeOptions()
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    all_funds_data = []

    try:
        print("正在前往國泰人壽基金網頁...", flush=True)
        driver.get(url)
        wait = WebDriverWait(driver, 15)

        try:
            wait.until(EC.frame_to_be_available_and_switch_to_it((By.TAG_NAME, "iframe")))
        except TimeoutException:
            print("❌ 找不到 iframe，請確認網頁結構。", flush=True)
            return None

        select_locator = (By.XPATH, "//select[option[contains(text(), '壽險') or contains(text(), '保險') or contains(text(), '請選擇')]]")
        try:
            wait.until(EC.presence_of_element_located(select_locator))
        except TimeoutException:
            print("\n❌ 找不到保險商品選單！", flush=True)
            return None

        select_element = driver.find_element(*select_locator)
        select = Select(select_element)

        exclude_words = [
            '', '請選擇', '請選擇保險商品', '請選擇商品',
            '--依風險等級查詢--', '--依保險商品名稱查詢--', '---請選擇---'
        ]
        product_names = [option.text.strip() for option in select.options if option.text.strip() not in exclude_words]

        print(f"✅ 共找到 {len(product_names)} 個保險商品，開始逐一爬取...\n", flush=True)

        for index, product in enumerate(product_names, 1):
            print(f"[{index}/{len(product_names)}] 正在爬取：{product} ...", flush=True)

            # 防卡死機制：每爬 15 個商品，重整網頁釋放記憶體
            if index > 1 and index % 15 == 0:
                print("  └─ 🔄 [系統排毒] 重新載入網頁，釋放瀏覽器記憶體...", flush=True)
                try:
                    driver.get(url)
                    time.sleep(3)
                    wait.until(EC.frame_to_be_available_and_switch_to_it((By.TAG_NAME, "iframe")))
                except Exception as e:
                    print(f"  └─ ⚠️ 重整網頁失敗，嘗試繼續... ({e})", flush=True)

            try:
                try:
                    driver.switch_to.alert.accept()
                except:
                    pass

                current_select_element = driver.find_element(*select_locator)
                current_select = Select(current_select_element)
                current_select.select_by_visible_text(product)

                time.sleep(3.5)

                try:
                    tab_xpath = "//a[@data-menutype='2' and contains(text(), '淨值/績效')]"
                    target_tab = wait.until(EC.presence_of_element_located((By.XPATH, tab_xpath)))
                    driver.execute_script("arguments[0].click();", target_tab)
                    time.sleep(3)
                except Exception:
                    print(f"  └─ ⚠️ 找不到指定的「淨值/績效」分頁標籤", flush=True)

                try:
                    length_select_elem = driver.find_element(By.NAME, "dataTbl_length")
                    length_select = Select(length_select_elem)
                    try:
                        length_select.select_by_value("-1")
                    except:
                        try:
                            length_select.select_by_value("all")
                        except:
                            length_select.select_by_index(len(length_select.options) - 1)
                    time.sleep(2.5)
                except Exception:
                    pass

                soup = BeautifulSoup(driver.page_source, 'html.parser')
                target_table = None
                for t in soup.find_all('table'):
                    if '代碼' in t.text and ('一個月' in t.text or '報酬' in t.text):
                        target_table = t
                        break

                if target_table:
                    html_io = StringIO(str(target_table))
                    df_list = pd.read_html(html_io)

                    if df_list:
                        df = df_list[0]
                        
                        # ==========================================
                        # 🛠️ 終極修復：座標定位強制命名法 (無視 Unnamed 垃圾標題)
                        # ==========================================
                        # 1. 安全解開多層合併儲存格
                        if isinstance(df.columns, pd.MultiIndex):
                            df.columns = [''.join(str(x) for x in col) for col in df.columns]

                        # 2. 尋找「代碼」這兩個字的確切座標 (索引值)
                        code_idx = -1
                        for i, col in enumerate(df.columns):
                            if '代碼' in str(col):
                                code_idx = i
                                break

                        if code_idx != -1:
                            # 3. 建立標準的 11 個欄位名稱
                            standard_cols = ['基金代碼', '基金名稱', '一個月％', '三個月％', '六個月％', '今年來％', '一年％', '二年％', '三年％', '五年％', '成立來％']
                            rename_dict = {}
                            
                            # 4. 【大絕招】從「代碼」的位置開始，往右數 11 格，硬生生把標題蓋上去！
                            # 這樣就算是 Unnamed，也會乖乖變成我們要的「二年％、三年％」
                            for i in range(min(11, len(df.columns) - code_idx)):
                                old_col_name = df.columns[code_idx + i]
                                rename_dict[old_col_name] = standard_cols[i]
                                
                            df = df.rename(columns=rename_dict)

                            # 5. 過濾掉這 11 格以外的隱藏垃圾欄位
                            keep_cols = [c for c in standard_cols if c in df.columns]
                            df = df[keep_cols]

                            # 6. 防呆補齊：如果原始表格根本不到 11 欄，主動補上空字串
                            for c in standard_cols:
                                if c not in df.columns:
                                    df[c] = ''
                                    
                            # 確保資料庫欄位順序 100% 正確
                            df = df[standard_cols]

                        # ==========================================

                        # 確保代碼欄位被當作字串讀取
                        if '基金代碼' in df.columns:
                            df['基金代碼'] = df['基金代碼'].astype(str)

                        # 插入保單名稱作為第一欄
                        df.insert(0, '保險商品名稱', product)
                        all_funds_data.append(df)
                        print(f"  └─ ✅ 成功取得 {len(df)} 筆基金績效資料\n", flush=True)
                    else:
                        print("  └─ ⚠️ 無法解析表格內容\n", flush=True)
                else:
                    print("  └─ ⚠️ 此商品查無表格資料\n", flush=True)

            except Exception as e:
                print(f"  └─ ❌ 爬取 {product} 時發生錯誤：{e}\n", flush=True)
                continue

    finally:
        driver.quit()
        print("\n網頁瀏覽器已關閉。", flush=True)

    if all_funds_data:
        final_df = pd.concat(all_funds_data, ignore_index=True)
        return final_df
    else:
        return None


if __name__ == "__main__":
    result_df = fetch_all_cathay_funds()

    if result_df is not None:
        print(f"\n🎉 爬取完成！總共取得 {len(result_df)} 筆資料。", flush=True)

        print("\n開始進行資料清洗...", flush=True)
        for col in ['保險商品名稱', '基金代碼', '基金名稱']:
            if col in result_df.columns:
                result_df[col] = result_df[col].astype(str).str.strip()

        # 將 NaN 替換為 '-' 讓前端沒有數據的欄位顯示比較漂亮
        result_df = result_df.fillna('-')
        print("✅ 資料清洗完畢。", flush=True)

        csv_filename = "Cathay_Performance_Funds_All.csv"
        result_df.to_csv(csv_filename, index=False, encoding="utf-8-sig")
        print(f"📁 資料已備份至本地 CSV：{csv_filename}", flush=True)

        db_name = 'cathay_funds.db'
        print(f"正在將資料存入 SQLite ({db_name})...", flush=True)
        try:
            conn = sqlite3.connect(db_name)
            result_df.to_sql('funds', conn, if_exists='replace', index=False)
            conn.commit()
            conn.close()
            print(f"✅ SQLite 建置完成！【全部 11 個績效欄位】都已完整寫入。")
        except Exception as e:
            print(f"❌ 寫入 SQLite 時發生錯誤：{e}", flush=True)

        print("正在連線至 Google Sheets...", flush=True)
        try:
            gc = gspread.service_account(filename='credentials.json')
            spreadsheet = gc.open('國泰人壽基金績效表')
            worksheet = spreadsheet.sheet1
            worksheet.clear()
            header = result_df.columns.values.tolist()
            data_values = result_df.values.tolist()
            data_to_upload = [header] + data_values
            worksheet.update(values=data_to_upload, range_name='A1')
            print("✅ 成功！所有資料已同步至 Google Sheets。", flush=True)

        except FileNotFoundError:
            print("❌ 找不到 credentials.json 檔案！", flush=True)
        except gspread.exceptions.SpreadsheetNotFound:
            print("❌ 找不到指定的 Google 試算表，請確認名稱與共用權限！", flush=True)
        except Exception as e:
            print(f"❌ 寫入 Google Sheets 時發生錯誤：{e}", flush=True)

    else:
        print("\n❌ 未能取得任何資料。", flush=True)
