import os
import sqlite3
from flask import Flask, request, jsonify, render_template
from google import genai

app = Flask(__name__)

# ==========================================
# 🤖 Gemini AI 初始化設定區
# ==========================================
# 為了資安考量，上線雲端時必須從「環境變數」讀取金鑰。
# 若您在「本機電腦」測試遇到金鑰錯誤，可暫時將下方替換為：MY_API_KEY = "AIzaSy..."
MY_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

# 初始化最新版 Gemini Client
client = genai.Client(api_key=MY_API_KEY)

# ==========================================
# 📊 資料庫連線設定
# ==========================================
def get_db_connection():
    # 連線到我們每天早上 5 點由爬蟲自動更新的資料庫
    conn = sqlite3.connect('cathay_funds.db')
    conn.row_factory = sqlite3.Row
    return conn

# ==========================================
# 🌐 網頁與 API 路由設定
# ==========================================
@app.route('/')
def index():
    """載入前端精美網頁"""
    return render_template('index.html')

@app.route('/api/products')
def get_products():
    """從資料庫撈取所有保險商品名稱 (供前端下拉選單與搜尋使用)"""
    try:
        conn = get_db_connection()
        # 使用 DISTINCT 確保保單名稱不重複
        products = conn.execute('SELECT DISTINCT 保險商品名稱 FROM funds WHERE 保險商品名稱 IS NOT NULL').fetchall()
        conn.close()
        return jsonify([dict(row)['保險商品名稱'] for row in products])
    except sqlite3.OperationalError as e:
        print(f"🚨 資料庫讀取錯誤: {e}")
        return jsonify([]), 500

@app.route('/api/funds')
def get_funds():
    """根據客戶選擇的商品名稱，撈取對應的關聯基金與歷史績效"""
    product_name = request.args.get('product')
    try:
        conn = get_db_connection()
        # 精準撈取我們要的 11 個標準欄位
        query = '''
            SELECT 基金代碼, 基金名稱, 
                   [一個月％], [三個月％], [六個月％], [今年來％], 
                   [一年％], [二年％], [三年％], [五年％], [成立來％] 
            FROM funds WHERE 保險商品名稱 = ?
        '''
        funds = conn.execute(query, (product_name,)).fetchall()
        conn.close()
        return jsonify([dict(row) for row in funds])
    except sqlite3.OperationalError as e:
        print(f"🚨 基金資料讀取錯誤: {e}")
        return jsonify([]), 500

@app.route('/api/advice', methods=['POST'])
def get_ai_advice():
    """接收前端傳來的資料與設定，調用 Gemini AI 產生尊榮配置建議"""
    data = request.json
    product_name = data.get('product')
    strategy = data.get('strategy', '平衡')
    fund_count = data.get('fundCount', 3)
    funds_list = data.get('funds')

    # ==========================================
    # 🧠 動態生成 AI 提示詞邏輯 (支援 AI 全權委託模式)
    # ==========================================
    
    # 1. 判斷投資策略
    if strategy == "AI決定":
        strategy_instruction = "請根據這些基金的「近1個月與近1年績效」，100% 由你自行判斷當下最適合的總體經濟投資策略（要積極、平衡還是保守？），並在開頭向客戶說明你為何選擇這個策略。"
        display_strategy = "專家動態調控"
    else:
        strategy_instruction = f"""請根據客戶選擇的「{strategy}型」偏好進行配置：
        - 積極型：大幅提高衛星部位(股票型/高成長)比例。
        - 平衡型：核心(防禦)與衛星(攻擊)部位應相對均衡。
        - 保守型：高度集中於核心部位(債券、收息、類全委帳戶)。"""
        display_strategy = strategy

    # 2. 判斷基金檔數
    if str(fund_count) == "AI決定":
        count_instruction = "請完全自行決定最適合的「基金配置檔數」（建議落在 2~6 檔之間即可），以達到最佳投資效率與風險分散，不需要湊滿特定數量。"
        display_count = "最佳"
    else:
        count_instruction = f"【強制要求】請嚴格挑選出「剛好 {fund_count} 檔」最適合的基金，不可多也不可少！"
        display_count = str(fund_count)

    # 3. 組合最終給 AI 的 Prompt
    prompt = f"""
    你現在是擁有10年經驗的「國泰基金投資專家」。
    客戶持有的保險商品為：{product_name}。
    該商品可選擇的基金標的，以及它們的「近期績效數據」如下：
    
    {funds_list}

    任務與規則：
    1. 只能從上述名單中挑選基金，嚴禁推薦名單外的標的。
    2. {count_instruction}
    3. 所有挑選出來的基金，配置比例(%) 總和必須剛好是 100%。
    4. {strategy_instruction}
    5. 說明入選原因時，必須具體引用提供的「近1個月」或「近1年」績效數據來佐證你的專業度。
    6. 語氣需極度親切、白話、尊榮，讓高資產客戶感到安心。
    
    7. 【高齡友善 HTML 排版強制要求】請完全依照以下 HTML 結構輸出，絕對不要使用 Markdown 語法：
       <h3 class="advice-title">💡 您的【{display_strategy}型】專屬投資配置建議 (共 {display_count} 檔)</h3>
       <p class="intro-text">根據您目前的保單與最新市場動能，為您精選了以下標的：</p>
       
       <div class="allocation-card core">
           <div class="badge">🛡️ 核心穩健配置 (或 🚀 衛星成長配置)</div>
           <h4>基金名稱 (建議佔比 X%)</h4>
           <p><strong>推薦原因：</strong> (說明原因與引用的績效數據)</p>
       </div>
       
       <div class="warm-reminder">
           <strong>👨‍💼 專家溫馨提醒：</strong> (結語，提醒投資均有風險，建議可定期檢視)
       </div>
    """

    try:
        print(f"💡 準備傳送資料給 Gemini AI (策略:{strategy} / 檔數:{fund_count})...")
        # 呼叫最新版的 Gemini 模型
        response = client.models.generate_content(
            model='gemini-2.5-flash-lite',
            contents=prompt
        )
        print("✅ AI 分析完成！準備回傳至前端。")
        return jsonify({"advice": response.text})
        
    except Exception as e:
        print("🚨 後台捕捉到 AI 連線錯誤：", e)
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    # 啟動 Flask 本機伺服器
    app.run(debug=True, port=5000)
