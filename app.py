import os
import sqlite3
from flask import Flask, request, jsonify, render_template
from google import genai

app = Flask(__name__)

# ==========================================
# 🤖 Gemini AI 初始化設定區
# ==========================================
# 本機測試時，您可以暫時把 "" 換成您的 "AIzaSy..." 金鑰。
# 部署到 Render 雲端時，請保持原本的 os.environ.get 寫法，以確保資安！
MY_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

# 初始化最新版 Client
client = genai.Client(api_key=MY_API_KEY)

# ==========================================
# 📊 資料庫連線設定
# ==========================================
def get_db_connection():
    conn = sqlite3.connect('cathay_funds.db')
    conn.row_factory = sqlite3.Row
    return conn

# ==========================================
# 🌐 網頁與 API 路由設定
# ==========================================
@app.route('/')
def index():
    """載入前端網頁"""
    return render_template('index.html')

@app.route('/api/products')
def get_products():
    """從資料庫撈取所有保險商品名稱 (供下拉選單與搜尋使用)"""
    try:
        conn = get_db_connection()
        products = conn.execute('SELECT DISTINCT 保險商品名稱 FROM funds WHERE 保險商品名稱 IS NOT NULL').fetchall()
        conn.close()
        return jsonify([dict(row)['保險商品名稱'] for row in products])
    except sqlite3.OperationalError as e:
        print(f"資料庫錯誤: {e}")
        return jsonify([]), 500

@app.route('/api/funds')
def get_funds():
    """根據商品名稱撈取對應的基金 (包含所有歷史績效)"""
    product_name = request.args.get('product')
    conn = get_db_connection()
    
    query = '''
        SELECT 基金代碼, 基金名稱, 
               [一個月％], [三個月％], [六個月％], [今年來％], 
               [一年％], [二年％], [三年％], [五年％], [成立來％] 
        FROM funds WHERE 保險商品名稱 = ?
    '''
    funds = conn.execute(query, (product_name,)).fetchall()
    conn.close()
    return jsonify([dict(row) for row in funds])

@app.route('/api/advice', methods=['POST'])
def get_ai_advice():
    """接收前端傳來的資料，調用 Gemini AI 產生配置建議"""
    data = request.json
    product_name = data.get('product')
    strategy = data.get('strategy', '平衡')
    fund_count = data.get('fundCount', 3)
    funds_list = data.get('funds')

    # ==========================================
    # 🧠 動態生成 AI 提示詞邏輯 (支援全權委託模式)
    # ==========================================
    
    # 1. 判斷投資策略
    if strategy == "AI決定":
        strategy_instruction = "請根據這些基金的「近1個月與近1年績效」，100% 由你自行判斷當下最適合的投資策略（要積極、平衡還是保守？），並在開頭向客戶說明你為何選擇這個策略。"
        display_strategy = "專家動態調控"
    else:
        strategy_instruction = f"""請根據客戶選擇的「{strategy}型」偏好進行配置：
        - 積極型：大幅提高衛星部位(股票型/高成長)比例。
        - 平衡型：核心(防禦)與衛星(攻擊)部位應相對均衡。
        - 保守型：高度集中於核心部位(債券、收息、類全委帳戶)。"""
        display_strategy = strategy

    # 2. 判斷基金檔數
    if str(fund_count) == "AI決定":
        count_instruction = "請完全自行決定最適合的「基金配置檔數」（建議落在 2~6 檔之間即可），以達到最佳投資效率，不需要湊滿特定數量。"
        display_count = "最佳"
    else:
        count_instruction = f"【強制要求】請嚴格挑選出「剛好 {fund_count} 檔」最適合的基金，不可多也不可少！"
        display_count = str(fund_count)

    # 3. 組合最終 Prompt
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
    5. 說明入選原因時，必須具體引用提供的「近1個月」或「近1年」績效數據來佐證。
    6. 語氣需極度親切、白話、尊榮。
    
    7. 【高齡友善 HTML 排版強制要求】請依照以下 HTML 結構輸出，不要使用 Markdown：
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
        print(f"💡 傳送資料給 AI (策略:{strategy} / 檔數:{fund_count})...")
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
    app.run(debug=True, port=5000)

