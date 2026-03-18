import os
import sqlite3
from flask import Flask, request, jsonify, render_template
from google import genai

app = Flask(__name__)

# ==========================================
# ⚙️ 環境與路徑設定
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'cathay_funds.db')

# ==========================================
# 🤖 Gemini AI 初始化設定區
# ==========================================
MY_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

if not MY_API_KEY:
    print("⚠️ 警告：未偵測到 GEMINI_API_KEY 環境變數，AI 功能將無法正常運作！")

# 延遲初始化，避免啟動時卡住
client = None

def get_ai_client():
    global client
    if client is None and MY_API_KEY:
        client = genai.Client(api_key=MY_API_KEY)
    return client


# ==========================================
# 📊 資料庫連線設定
# ==========================================
def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ==========================================
# 🌐 網頁與 API 路由設定
# ==========================================
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/products')
def get_products():
    try:
        with get_db_connection() as conn:
            products = conn.execute('SELECT DISTINCT 保險商品名稱 FROM funds WHERE 保險商品名稱 IS NOT NULL').fetchall()
        return jsonify([dict(row)['保險商品名稱'] for row in products])
    except sqlite3.OperationalError as e:
        print(f"🚨 資料庫錯誤 (讀取保單清單): {e}")
        return jsonify([]), 500


@app.route('/api/funds')
def get_funds():
    product_name = request.args.get('product')
    try:
        with get_db_connection() as conn:
            query = 'SELECT * FROM funds WHERE 保險商品名稱 = ?'
            rows = conn.execute(query, (product_name,)).fetchall()

        funds_data = []
        for row in rows:
            fund_dict = dict(row)
            fund_dict['基金代碼'] = fund_dict.get('基金代碼') or fund_dict.get('代碼', '')
            fund_dict['基金名稱'] = fund_dict.get('基金名稱') or fund_dict.get('名稱', '')
            funds_data.append(fund_dict)

        return jsonify(funds_data)

    except sqlite3.OperationalError as e:
        print(f"🚨 資料庫錯誤 (讀取基金列表): {e}")
        return jsonify([]), 500


@app.route('/api/advice', methods=['POST'])
def get_ai_advice():
    if not get_ai_client():
        return jsonify({"error": "伺服器未設定 AI 金鑰，請聯繫管理員。"}), 500

    data = request.json
    product_name = data.get('product')
    strategy = data.get('strategy', '平衡')
    fund_count = data.get('fundCount', 4)
    funds_list = data.get('funds')

    if strategy == "AI決定":
        strategy_instruction = "請根據這些基金的「近1個月與3個月、6個月、今年以來、近1年績效」，100% 由你自行判斷當下最適合的投資策略（要積極、平衡還是保守？），並在開頭向客戶說明你為何選擇這個策略。"
        display_strategy = "專家動態調控"
    else:
        strategy_instruction = f"""請根據客戶選擇的「{strategy}型」偏好進行配置：
        - 積極型：以股票型/高成長基金為主,分析近1個月與3個月、6個月、今年以來、近1年績效」，判斷當下最適合的投資策略。
        - 平衡型：核心(防禦)與衛星(攻擊)部位應相對均衡。
        - 保守型：高度集中於核心部位(債券、收息、類全委帳戶)。"""
        display_strategy = strategy

    if str(fund_count) == "AI決定":
        count_instruction = "請完全自行決定最適合的「基金配置檔數」（建議落在 2~8 檔之間即可），以達到最佳投資效率，不需要湊滿特定數量。"
        display_count = "最佳"
    else:
        count_instruction = f"【強制要求】請嚴格挑選出「剛好 {fund_count} 檔」最適合的基金，不可多也不可少！"
        display_count = str(fund_count)

    prompt = f"""
    請扮演一位資深的專業理財顧問與基金分析師。我正在尋找適合的基金投資標的，請協助我進行篩選與客觀的量化/質化分析。
    客戶持有的保險商品為：{product_name}。
    該商品可選擇的基金標的，以及它們的「近期績效數據」如下：

    {funds_list}
    
    核心績效評估：
    1.找出在**二年％報酬率中排名前 10%，且一年％績效也排名前 10% 的「長期贏家」**基金名單。
    2.找出在**二年％、三年％表現相對穩健，但一年％和今年來％跌幅最小的「抗跌/穩健」**基金名單。
    
    任務與規則：
    1. 只能從上述名單中挑選基金，嚴禁推薦名單外的標的。
    2. {count_instruction}
    3. 所有挑選出來的基金，配置比例(%) 總和必須剛好是 100%。
    4. {strategy_instruction}
    5. 說明入選原因時，並在開頭向客戶說明你為何選擇這個策略,必須具體引用提供的「近1個月」或「近1年」績效數據來佐證。
    6. 基金核心持股分析：列出前幾大持股或主要產業佈局，並分析其是否符合目前的總體經濟趨勢。
    7. 進退場機制建議：針對衛星部位的波動性，設定合理的停利與停損建議（例如回檔多少 % 應考慮調節），並提醒潛在風險。
    8. 語氣需極度親切、白話、尊榮。
    
    9. 【高齡友善 HTML 排版強制要求】請依照以下 HTML 結構輸出：
       <h3 class="advice-title">💡 您的【{display_strategy}型】專屬投資配置建議 (共 {display_count} 檔)</h3>
       <p class="intro-text">根據您目前的保單與最新市場動能，為您精選了以下標的：</p>

       <div class="allocation-card core">
           <div class="badge">🛡️ 核心穩健配置 (或 🚀 衛星成長配置)</div>
           <h4>基金名稱 (建議佔比 X%)</h4>
           <p><strong>推薦原因：</strong> (說明原因與引用的績效數據)</p>
       </div>

       <div class="warm-reminder">
           <strong>👨‍💼 專家溫馨提醒：</strong> (結語，提醒投資均有風險)
       </div>
    """

    try:
        print(f"💡 傳送資料給 AI (策略:{strategy} / 檔數:{fund_count})...")
        response = get_ai_client().models.generate_content(
            model='gemini-3.1-flash-lite-preview',
            contents=prompt
        )
        print("✅ AI 分析完成！準備回傳至前端。")
        return jsonify({"advice": response.text})

    except Exception as e:
        print("🚨 後台捕捉到 AI 連線錯誤：", e)
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    is_debug = os.environ.get("FLASK_ENV") == "development"
    app.run(host='0.0.0.0', port=port, debug=is_debug)
