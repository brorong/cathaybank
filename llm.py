"""
🤖 免費 LLM 供應商串接 (自動備援)

依序嘗試下列「有免費額度」的 API，前一個失敗 (額度用完 429 / 伺服器錯誤) 就自動換下一個：
  1. Google Gemini   (GEMINI_API_KEY)      https://aistudio.google.com/apikey
  2. Groq            (GROQ_API_KEY)        https://console.groq.com/keys
  3. OpenRouter      (OPENROUTER_API_KEY)  https://openrouter.ai/keys  (僅使用 :free 模型)

只要設定其中任一把金鑰即可運作；三把都設定則可大幅降低「額度用完」的機率。
模型清單可用環境變數覆寫 (逗號分隔)：GEMINI_MODELS / GROQ_MODELS / OPENROUTER_MODELS
"""
import os
import json
import re

import requests

TIMEOUT = int(os.environ.get("LLM_TIMEOUT", 90))


def _env_list(name, default):
    return [m.strip() for m in os.environ.get(name, default).split(",") if m.strip()]


def _key(name):
    return os.environ.get(name, "").strip()


class LLMError(Exception):
    pass


# ==========================================
# 各家 API 呼叫
# ==========================================
def _call_gemini(model, prompt, api_key):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.4, "responseMimeType": "application/json"},
    }
    r = requests.post(url, params={"key": api_key}, json=body, timeout=TIMEOUT)
    if r.status_code != 200:
        raise LLMError(f"HTTP {r.status_code}: {r.text[:200]}")
    data = r.json()
    try:
        return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError):
        raise LLMError(f"回應格式異常：{str(data)[:200]}")


def _call_openai_compatible(base_url, model, prompt, api_key, json_mode=True):
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是專業理財顧問，只輸出合法 JSON，不要輸出任何其他文字。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.4,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    r = requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json=body,
        timeout=TIMEOUT,
    )
    if r.status_code != 200:
        raise LLMError(f"HTTP {r.status_code}: {r.text[:200]}")
    try:
        return r.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        raise LLMError(f"回應格式異常：{r.text[:200]}")


def available_providers():
    """回傳 [(顯示名稱, 呼叫函式)]，依優先順序排列"""
    chain = []

    if key := _key("GEMINI_API_KEY"):
        for m in _env_list("GEMINI_MODELS", "gemini-3.5-flash,gemini-3.1-flash-lite,gemini-flash-latest"):
            chain.append((f"Google Gemini ({m})", lambda p, m=m: _call_gemini(m, p, key)))

    if key := _key("GROQ_API_KEY"):
        for m in _env_list("GROQ_MODELS", "llama-3.3-70b-versatile"):
            chain.append((f"Groq ({m})", lambda p, m=m: _call_openai_compatible(
                "https://api.groq.com/openai/v1", m, p, key)))

    if key := _key("OPENROUTER_API_KEY"):
        for m in _env_list("OPENROUTER_MODELS", "meta-llama/llama-3.3-70b-instruct:free"):
            chain.append((f"OpenRouter ({m})", lambda p, m=m: _call_openai_compatible(
                "https://openrouter.ai/api/v1", m, p, key, json_mode=False)))

    return chain


# ==========================================
# JSON 解析 (容忍 ```json 包裝或前後多餘文字)
# ==========================================
def parse_json(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


def generate_json(prompt):
    """依序嘗試所有免費供應商，回傳 (解析後的 dict, 供應商名稱)"""
    chain = available_providers()
    if not chain:
        raise LLMError("未設定任何 AI 金鑰 (GEMINI_API_KEY / GROQ_API_KEY / OPENROUTER_API_KEY)")

    errors = []
    for name, call in chain:
        try:
            print(f"💡 嘗試 {name} ...", flush=True)
            result = parse_json(call(prompt))
            if not isinstance(result, dict) or not result.get("allocations"):
                raise LLMError("JSON 缺少 allocations 欄位")
            print(f"✅ {name} 分析完成", flush=True)
            return result, name
        except (LLMError, requests.RequestException, ValueError) as e:
            print(f"  └─ ⚠️ {name} 失敗，改用下一個：{e}", flush=True)
            errors.append(f"{name}: {e}")

    raise LLMError("所有免費 AI 供應商皆失敗：\n" + "\n".join(errors))
