import os
import sqlite3
from contextlib import closing
from datetime import datetime
from html import escape

from flask import Flask, request, jsonify, render_template

import llm

app = Flask(__name__)

# ==========================================
# ⚙️ 環境與路徑設定
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'cathay_funds.db')

PERF_COLS = ['一個月％', '三個月％', '六個月％', '今年來％', '一年％', '二年％', '三年％', '五年％', '成立來％']
# 卡片上顯示的績效欄位 (數字直接取自資料庫，不經 AI，避免數據被改寫)
CARD_PERF_COLS = ['一個月％', '三個月％', '今年來％', '一年％', '二年％', '三年％']

if not llm.available_providers():
    print("⚠️ 警告：未偵測到 GEMINI_API_KEY / GROQ_API_KEY / OPENROUTER_API_KEY，AI 功能將無法正常運作！")


# ==========================================
# 📊 資料庫連線設定
# ==========================================
def execute_query(query, params=()):
    """共用資料庫查詢函式，確保連線會自動關閉，避免資源耗盡"""
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(query, params).fetchall()


def load_funds(product_name):
    funds = []
    for row in execute_query('SELECT * FROM funds WHERE 保險商品名稱 = ?', (product_name,)):
        fund = dict(row)
        # 統一欄位名稱，避免資料庫欄位不一致
        fund['基金代碼'] = str(fund.get('基金代碼') or fund.get('代碼', '')).strip()
        fund['基金名稱'] = str(fund.get('基金名稱') or fund.get('名稱', '')).strip()
        funds.append(fund)
    return funds


def data_date():
    try:
        return datetime.fromtimestamp(os.path.getmtime(DB_PATH)).strftime('%Y/%m/%d')
    except OSError:
        return '-'


# ==========================================
# 🌐 網頁與 API 路由設定
# ==========================================
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/products')
def get_products():
    try:
        query = 'SELECT DISTINCT 保險商品名稱 FROM funds WHERE 保險商品名稱 IS NOT NULL'
        products = execute_query(query)
        return jsonify([dict(row)['保險商品名稱'] for row in products])
    except sqlite3.OperationalError as e:
        print(f"🚨 資料庫錯誤 (讀取保單清單): {e}")
        return jsonify([]), 500


@app.route('/api/funds')
def get_funds():
    try:
        return jsonify(load_funds(request.args.get('product')))
    except sqlite3.OperationalError as e:
        print(f"🚨 資料庫錯誤 (讀取基金列表): {e}")
        return jsonify([]), 500


# ==========================================
# 🧠 AI Prompt 產生器
# ==========================================
def format_funds_table(funds):
    """壓縮成精簡表格 (省 token)，並提供完整 9 個績效欄位給 AI"""
    header = '代碼|名稱|' + '|'.join(c.replace('％', '') for c in PERF_COLS)
    lines = [header]
    for f in funds:
        lines.append('|'.join([f['基金代碼'], f['基金名稱']] + [str(f.get(c, '-')) for c in PERF_COLS]))
    return '\n'.join(lines)


def build_ai_prompt(product_name, strategy, fund_count, funds):
    """根據使用者選擇，動態生成給 AI 的 Prompt (要求輸出 JSON，由後端統一排版)"""

    if strategy == "AI決定":
        strategy_instruction = "請根據各期間績效，100% 由你自行判斷最適合的策略（積極 / 平衡 / 保守），並說明理由。"
    else:
        strategy_instruction = f"""客戶選擇「{strategy}型」：
  - 積極型：以衛星(攻擊)部位為主，股票型/高成長基金。
  - 平衡型：核心(防禦)與衛星(攻擊)部位相對均衡。
  - 保守型：高度集中於核心部位(債券、收息、貨幣、類全委帳戶)。"""

    if str(fund_count) == "AI決定":
        count_instruction = "請自行決定最適合的配置檔數 (2~8 檔)，不需湊數。"
    else:
        count_instruction = f"【強制】必須剛好挑選 {fund_count} 檔，不可多也不可少。"

    return f"""你是一位資深專業理財顧問與基金分析師。
客戶持有的保險商品：{product_name}
可選基金與績效 (單位 %，"-" 表示無資料)：

{format_funds_table(funds)}

【篩選原則】
1. 長期贏家：二年報酬排名前 10%，且一年報酬也排名前 10%。
2. 抗跌穩健：二年、三年表現穩健，且一年、今年來跌幅最小。
3. 同一基金有（單位撥回）與（現金撥回）兩種時，優先選（單位撥回）。

【任務規則】
1. 只能從上表挑選，code 必須完全等於上表「代碼」。
2. {count_instruction}
3. 所有 weight 為整數，總和必須剛好 100。
4. {strategy_instruction}
5. 每檔 reason 必須具體引用上表的績效數字 (例如：近1年 +25.3%、二年 +48.1%)，2~3 句。
6. 說明主要產業佈局，並分析是否符合目前國際經濟趨勢。
7. 針對衛星部位設定具體停利/停損 % 建議，並提醒風險。
8. 語氣親切、白話、尊榮，適合長輩閱讀；避免艱深術語。

【輸出格式】只輸出以下 JSON，不要任何其他文字或 Markdown：
{{
  "strategy": "積極 | 平衡 | 保守 其中之一",
  "strategy_reason": "為何採用此策略 (2~3 句)",
  "market_view": "目前國際經濟趨勢與本配置的對應 (2~3 句)",
  "sectors": ["主要產業佈局1", "主要產業佈局2"],
  "allocations": [
    {{"code": "基金代碼", "role": "core 或 satellite", "weight": 25, "reason": "推薦原因並引用績效數字"}}
  ],
  "take_profit": "停利建議",
  "stop_loss": "停損 / 回檔調節建議",
  "risks": ["風險提醒1", "風險提醒2"],
  "closing": "溫馨結語"
}}"""


# ==========================================
# ✅ AI 結果檢核 (防止 AI 推薦名單外基金、比例加總錯誤)
# ==========================================
def normalize_weights(allocs):
    """把比例調整為整數且總和剛好 100 (最大餘數法)"""
    raw = [max(float(a.get('weight') or 0), 0) for a in allocs]
    total = sum(raw) or len(raw)
    if not sum(raw):
        raw = [1] * len(raw)
    exact = [w * 100 / total for w in raw]
    ints = [int(x) for x in exact]
    for i in sorted(range(len(exact)), key=lambda i: exact[i] - ints[i], reverse=True)[:100 - sum(ints)]:
        ints[i] += 1
    for a, w in zip(allocs, ints):
        a['weight'] = w


def validate_advice(result, funds, fund_count):
    by_code = {f['基金代碼']: f for f in funds}
    by_name = {f['基金名稱']: f for f in funds}

    allocs, seen = [], set()
    for a in result.get('allocations') or []:
        if not isinstance(a, dict):
            continue
        fund = by_code.get(str(a.get('code', '')).strip()) or by_name.get(str(a.get('name', '')).strip())
        if not fund or fund['基金代碼'] in seen:
            continue  # 名單外或重複的基金一律剔除
        seen.add(fund['基金代碼'])
        a['fund'] = fund
        a['role'] = 'satellite' if str(a.get('role', '')).lower().startswith('sat') else 'core'
        allocs.append(a)

    if str(fund_count).isdigit():
        allocs.sort(key=lambda a: float(a.get('weight') or 0), reverse=True)
        allocs = allocs[:int(fund_count)]

    if not allocs:
        raise llm.LLMError("AI 回傳的基金都不在可選名單內，請重新產生。")

    normalize_weights(allocs)
    allocs.sort(key=lambda a: (a['role'] != 'core', -a['weight']))
    result['allocations'] = allocs
    return result


# ==========================================
# 🎨 HTML 排版 (後端統一產生，格式穩定且已跳脫，避免 XSS)
# ==========================================
def _pct(v):
    try:
        n = float(v)
    except (TypeError, ValueError):
        return '<span class="perf-na">-</span>'
    cls = 'perf-up' if n > 0 else 'perf-down' if n < 0 else ''
    return f'<span class="{cls}">{n:+.2f}%</span>'


def _list(items):
    items = [i for i in (items or []) if str(i).strip()]
    return ''.join(f'<li>{escape(str(i))}</li>' for i in items)


def render_advice_html(result, strategy, provider):
    allocs = result['allocations']
    shown_strategy = result.get('strategy') or strategy
    core_w = sum(a['weight'] for a in allocs if a['role'] == 'core')
    sat_w = 100 - core_w
    auto_tag = '（AI 全權委託）' if strategy == 'AI決定' else ''

    cards = []
    for i, a in enumerate(allocs, 1):
        f = a['fund']
        is_core = a['role'] == 'core'
        perf = ''.join(
            f'<div class="perf-item"><span class="perf-label">{c.replace("％", "")}</span>{_pct(f.get(c))}</div>'
            for c in CARD_PERF_COLS)
        cards.append(f"""
        <div class="allocation-card {a['role']}">
            <div class="card-head">
                <span class="badge">{'🛡️ 核心穩健' if is_core else '🚀 衛星成長'}</span>
                <span class="weight">{a['weight']}%</span>
            </div>
            <h4>{i}. {escape(f['基金名稱'])} <small>({escape(f['基金代碼'])})</small></h4>
            <div class="weight-bar"><div style="width:{a['weight']}%"></div></div>
            <div class="perf-grid">{perf}</div>
            <p><strong>推薦原因：</strong>{escape(str(a.get('reason', '')))}</p>
        </div>""")

    summary_rows = ''.join(
        f"<tr><td>{escape(a['fund']['基金名稱'])}</td>"
        f"<td>{'🛡️ 核心' if a['role'] == 'core' else '🚀 衛星'}</td>"
        f"<td class='num'>{a['weight']}%</td></tr>"
        for a in allocs)

    sectors = ''.join(f'<span class="chip">{escape(str(s))}</span>' for s in result.get('sectors') or [])

    return f"""
    <h3 class="advice-title">💡 您的【{escape(shown_strategy)}型】專屬配置建議{auto_tag}（共 {len(allocs)} 檔）</h3>

    <div class="summary-box">
        <div class="split-bar">
            <div class="split-core" style="width:{core_w}%">{f'核心 {core_w}%' if core_w else ''}</div>
            <div class="split-sat" style="width:{sat_w}%">{f'衛星 {sat_w}%' if sat_w else ''}</div>
        </div>
        <p><strong>🎯 策略說明：</strong>{escape(str(result.get('strategy_reason', '')))}</p>
        <p><strong>🌏 市場觀點：</strong>{escape(str(result.get('market_view', '')))}</p>
        {f'<p><strong>🏭 產業佈局：</strong>{sectors}</p>' if sectors else ''}
    </div>

    <div class="allocation-container">{''.join(cards)}</div>

    <h4 class="section-title">📋 配置總覽</h4>
    <table class="summary-table">
        <thead><tr><th>基金名稱</th><th>角色</th><th>比例</th></tr></thead>
        <tbody>{summary_rows}<tr class="total"><td>合計</td><td></td><td class="num">100%</td></tr></tbody>
    </table>

    <div class="exit-plan">
        <h4 class="section-title">🚦 進退場建議</h4>
        <p><strong>✅ 停利：</strong>{escape(str(result.get('take_profit', '')))}</p>
        <p><strong>⛔ 停損 / 調節：</strong>{escape(str(result.get('stop_loss', '')))}</p>
        {f'<ul class="risk-list">{_list(result.get("risks"))}</ul>' if _list(result.get('risks')) else ''}
    </div>

    <div class="warm-reminder">
        <strong>👨‍💼 專家溫馨提醒：</strong>{escape(str(result.get('closing', '')))}
        <br><small>※ 投資一定有風險，基金投資有賺有賠，過去績效不代表未來表現，本建議僅供參考。</small>
    </div>

    <p class="advice-meta">資料日期：{data_date()} ｜ 分析引擎：{escape(provider)}</p>
    """


@app.route('/api/advice', methods=['POST'])
def get_ai_advice():
    if not llm.available_providers():
        return jsonify({"error": "伺服器未設定 AI 金鑰，請聯繫管理員。"}), 500

    data = request.get_json(silent=True) or {}
    product_name = data.get('product')
    strategy = data.get('strategy', '平衡')
    fund_count = data.get('fundCount', 4)

    try:
        funds = load_funds(product_name)
    except sqlite3.OperationalError as e:
        return jsonify({"error": f"資料庫錯誤：{e}"}), 500
    if not funds:
        return jsonify({"error": "找不到此保險商品的基金資料。"}), 400

    prompt = build_ai_prompt(product_name, strategy, fund_count, funds)

    try:
        print(f"💡 傳送資料給 AI (策略:{strategy} / 檔數:{fund_count} / 基金數:{len(funds)})...")
        result, provider = llm.generate_json(prompt)
        result = validate_advice(result, funds, fund_count)
        return jsonify({"advice": render_advice_html(result, strategy, provider)})
    except Exception as e:
        print("🚨 後台捕捉到 AI 錯誤：", e)
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    is_debug = os.environ.get("FLASK_ENV") == "development"
    app.run(host='0.0.0.0', port=port, debug=is_debug)
