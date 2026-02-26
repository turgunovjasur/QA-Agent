import google.generativeai as genai
import base64
import json
import os
import time
from dotenv import load_dotenv

load_dotenv()

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel("gemini-2.5-flash")

# Global token counter
token_stats = {"input": 0, "output": 0, "calls": 0}


def _extract_json(text: str) -> dict:
    text = text.strip()
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            if part.startswith("json"):
                text = part[4:].strip()
                break
            elif "{" in part:
                text = part.strip()
                break
    try:
        return json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except Exception:
                pass
    return {}


def _call_gemini(parts: list, step_name: str = "") -> tuple:
    """
    Gemini chaqiradi. Rate limit bo'lsa kutadi va qayta urinadi.
    Returns: (response_text, token_info)
    """
    max_retries = 3
    retry_delay = 25  # soniya

    for attempt in range(max_retries):
        try:
            response = model.generate_content(parts)

            usage = response.usage_metadata
            input_tokens = getattr(usage, "prompt_token_count", 0)
            output_tokens = getattr(usage, "candidates_token_count", 0)

            token_stats["input"] += input_tokens
            token_stats["output"] += output_tokens
            token_stats["calls"] += 1

            token_info = {
                "step": step_name,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
                "cumulative_total": token_stats["input"] + token_stats["output"],
                "api_calls": token_stats["calls"],
            }

            print(
                f"  [TOKEN] {step_name}: "
                f"in={input_tokens} out={output_tokens} "
                f"| Jami: {token_stats['input'] + token_stats['output']} token "
                f"({token_stats['calls']} API call)"
            )
            return response.text, token_info

        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "quota" in err_str.lower() or "RESOURCE_EXHAUSTED" in err_str:
                # retry_delay ni xatolik xabaridan olishga harakat
                import re
                m = re.search(r"retry_delay\s*\{\s*seconds:\s*(\d+)", err_str)
                wait = int(m.group(1)) + 3 if m else retry_delay

                print(f"\n  [⚠️  RATE LIMIT] Gemini kvota limitiga yetdi!")
                print(f"  [⏳ KUTISH] {wait} soniya kutilmoqda... (urinish {attempt+1}/{max_retries})")
                for remaining in range(wait, 0, -5):
                    print(f"  [⏳] {remaining}s qoldi...", end="\r")
                    time.sleep(5)
                print(f"  [✅ DAVOM] Qayta urinilmoqda...")
            else:
                raise e

    raise Exception(f"Gemini {max_retries} urinishdan keyin ham javob bermadi")


def reset_token_stats():
    token_stats["input"] = 0
    token_stats["output"] = 0
    token_stats["calls"] = 0


def get_token_summary() -> dict:
    return {
        "total_input": token_stats["input"],
        "total_output": token_stats["output"],
        "total_tokens": token_stats["input"] + token_stats["output"],
        "total_api_calls": token_stats["calls"],
    }


def parse_user_prompt(prompt: str) -> tuple:
    system = """
Siz professional QA test agentisiz.
Foydalanuvchi beradigan test buyrug'ini tahlil qilib, bajarish uchun step-by-step checklist tuzing.

Faqat quyidagi JSON formatda javob bering (boshqa hech narsa yozmang):
{
    "site_url": "https://example.com",
    "test_name": "Test nomi",
    "steps": [
        {
            "step_id": 1,
            "description": "Qadamni inson tilidagi tavsifi",
            "action_type": "navigate|login|find_and_click|find_and_fill|verify|wait",
            "expected_result": "Bu qadam muvaffaqiyatli bo'lsa nima ko'rinadi"
        }
    ]
}

action_type:
- navigate: URL ga o'tish
- login: saytga kirish (email/parol)
- find_and_click: elementni topib bosish
- find_and_fill: forma to'ldirish
- verify: natijani tekshirish
- wait: sahifa yuklanishini kutish

MUHIM: login stepsini FAQAT bitta qiling, login ni find_and_fill ga ajratmang!
"""
    text, token_info = _call_gemini([system, prompt], step_name="parse_prompt")
    result = _extract_json(text)
    if not result:
        result = {
            "site_url": None,
            "test_name": prompt[:50],
            "steps": [{"step_id": 1, "description": prompt,
                       "action_type": "navigate", "expected_result": ""}]
        }
    return result, token_info


def analyze_page(screenshot_bytes: bytes, task: str, page_url: str,
                 user_hint: str = None) -> dict:
    """
    Sahifa screenshotini tahlil qiladi.
    Barcha topilgan elementlarni va locatorlarni qaytaradi.
    user_hint: user bergan yo'nalish (masalan: "Spravichnik menyusida Tovarlar bor")
    """
    image_data = base64.b64encode(screenshot_bytes).decode("utf-8")

    hint_block = ""
    if user_hint:
        hint_block = f"\nFoydalanuvchi yo'riqnomasi: \"{user_hint}\"\nBu yo'riqnomaga asosan sahifada elementni toping.\n"

    system = f"""
Siz web sahifa tahlilchisisiz.
Joriy sahifa URL: {page_url}
Vazifa: {task}{hint_block}

Screenshotni diqqat bilan ko'rib, FAQAT quyidagi JSON formatda javob bering:
{{
    "page_type": "login|dashboard|form|list|detail|error|other",
    "page_title": "sahifa sarlavhasi yoki asosiy kontent nomi",
    "page_description": "sahifada nima ko'rinmoqda (qisqacha 1-2 gap)",
    "task_possible": true/false,
    "task_possible_reason": "nima uchun mumkin yoki mumkin emas",
    "found_elements": [
        {{
            "name": "element_nomi (snake_case)",
            "type": "input|button|link|select|textarea|checkbox",
            "visible_text": "elementda ko'ringan matn yoki placeholder",
            "css_selector": "eng aniq CSS selector",
            "xpath": "//tag[@attr='value'] kabi xpath",
            "location": "top-left|top-center|top-right|center|bottom"
        }}
    ],
    "login_detected": false,
    "error_detected": false,
    "error_text": "agar sahifada xato xabari bo'lsa u matn"
}}

CSS selector qoidalari (MUHIM!):
1. id bo'lsa: #element-id
2. name bo'lsa: input[name='fieldname']
3. placeholder bo'lsa: input[placeholder='matn']
4. type bo'lsa: input[type='password']
5. class + text bo'lsa: button.classname yoki button:has-text('matn')
6. Hech narsa bo'lmasa: //tag[contains(text(),'matn')]

HECH QACHON contains() CSS da ishlatma — faqat XPath da!
"""

    text, token_info = _call_gemini(
        [system, {"mime_type": "image/png", "data": image_data}],
        step_name="analyze_page"
    )

    # DEBUG: AI ning to'liq javobini chop etamiz
    print(f"\n  ┌─ [DEBUG: AI TAHLIL NATIJASI] ─────────────────────")
    result = _extract_json(text)
    print(f"  │ Sahifa turi   : {result.get('page_type', '?')}")
    print(f"  │ Sahifa nomi   : {result.get('page_title', '?')}")
    print(f"  │ Holat         : {result.get('page_description', '?')}")
    print(f"  │ Vazifa mumkin : {result.get('task_possible', '?')} — {result.get('task_possible_reason', '')}")
    print(f"  │ Login aniqlandi: {result.get('login_detected', False)}")
    print(f"  │ Xato bor      : {result.get('error_detected', False)}")
    if result.get('error_text'):
        print(f"  │ Xato matni    : {result.get('error_text')}")
    els = result.get("found_elements", [])
    print(f"  │ Topilgan elementlar ({len(els)} ta):")
    for el in els:
        print(f"  │   [{el.get('type','?')}] '{el.get('name','')}' | text='{el.get('visible_text','')}' | css='{el.get('css_selector','')}' | xpath='{el.get('xpath','')}'")
    print(f"  └───────────────────────────────────────────────────")

    result["_token_info"] = token_info
    return result


def analyze_form_page(screenshot_bytes: bytes, form_purpose: str, page_url: str) -> dict:
    image_data = base64.b64encode(screenshot_bytes).decode("utf-8")

    system = f"""
Siz web forma tahlilchisisiz.
Joriy sahifa URL: {page_url}
Forma maqsadi: {form_purpose}

Screenshotdagi BARCHA forma maydonlarini aniqlab, FAQAT JSON qaytaring:
{{
    "form_title": "forma sarlavhasi",
    "form_found": true/false,
    "fields": [
        {{
            "field_id": 1,
            "name": "maydon_nomi",
            "label": "ko'rinuvchi label",
            "type": "text|email|password|number|select|textarea|checkbox|radio|date",
            "placeholder": "placeholder yoki null",
            "required": true/false,
            "css_selector": "aniq CSS selector",
            "xpath": "aniq xpath"
        }}
    ],
    "submit_button": {{
        "text": "tugma matni",
        "css_selector": "selector",
        "xpath": "xpath"
    }}
}}

CSS selector qoidalari:
- id bo'lsa: #id-name
- name bo'lsa: input[name='name']
- placeholder bo'lsa: input[placeholder='hint']
- HECH QACHON contains() CSS da ishlatma
"""

    text, token_info = _call_gemini(
        [system, {"mime_type": "image/png", "data": image_data}],
        step_name="analyze_form"
    )

    result = _extract_json(text)

    # DEBUG
    print(f"\n  ┌─ [DEBUG: FORMA TAHLILI] ───────────────────────────")
    print(f"  │ Forma nomi  : {result.get('form_title', '?')}")
    print(f"  │ Forma topildi: {result.get('form_found', '?')}")
    fields = result.get("fields", [])
    print(f"  │ Maydonlar ({len(fields)} ta):")
    for f in fields:
        print(f"  │   [{f.get('type','?')}] '{f.get('label','')}' | css='{f.get('css_selector','')}' | xpath='{f.get('xpath','')}'")
    sub = result.get("submit_button") or {}
    print(f"  │ Submit: '{sub.get('text','')}' | css='{sub.get('css_selector','')}' | xpath='{sub.get('xpath','')}'")
    print(f"  └───────────────────────────────────────────────────")

    result["_token_info"] = token_info
    return result


def decide_field_value(field_info: dict, context: dict) -> dict:
    system = """
Siz QA test ma'lumotlari generatorsiz.
Forma maydoni uchun mos test qiymat taklif qiling.

FAQAT JSON qaytaring:
{
    "value": "kiritilishi kerak bo'lgan qiymat",
    "needs_user_input": false,
    "user_question": "agar needs_user_input=true bo'lsa savol"
}

Qoidalar:
- name/nomi/title → "Test Mahsulot 001"
- price/narx/cost → "99000"
- description/tavsif → "Test uchun kiritilgan tavsif"
- code/kod/sku → "TST-001"
- email → "test@test.com"
- Agar maydon noaniq yoki muhim bo'lsa → needs_user_input: true
"""
    prompt = f"Maydon: {json.dumps(field_info, ensure_ascii=False)}\nKontekst: {json.dumps(context, ensure_ascii=False)}"
    text, token_info = _call_gemini([system, prompt], step_name="decide_value")
    result = _extract_json(text)
    result["_token_info"] = token_info
    return result


def verify_action_result(screenshot_bytes: bytes, expected: str, page_url: str) -> dict:
    image_data = base64.b64encode(screenshot_bytes).decode("utf-8")

    system = f"""
Siz QA test natijasi tekshiruvchisisiz.
Sahifa URL: {page_url}
Kutilgan natija: {expected}

FAQAT JSON qaytaring:
{{
    "success": true/false,
    "current_state": "hozir sahifada nima ko'rinmoqda",
    "error_message": "xato matni yoki null",
    "confidence": 0.0-1.0
}}
"""
    text, token_info = _call_gemini(
        [system, {"mime_type": "image/png", "data": image_data}],
        step_name="verify_result"
    )
    result = _extract_json(text)

    # DEBUG
    print(f"\n  ┌─ [DEBUG: VERIFY NATIJASI] ─────────────────────────")
    print(f"  │ Muvaffaqiyat  : {result.get('success')}")
    print(f"  │ Joriy holat   : {result.get('current_state')}")
    print(f"  │ Xato          : {result.get('error_message')}")
    print(f"  │ Ishonch darajasi: {result.get('confidence')}")
    print(f"  └───────────────────────────────────────────────────")

    result["_token_info"] = token_info
    return result


def analyze_stuck_page(screenshot_bytes: bytes, expected_action: str, page_url: str) -> dict:
    """
    URL o'zgarmadi — sahifada nima muammo borligini tahlil qiladi.
    """
    image_data = base64.b64encode(screenshot_bytes).decode("utf-8")

    system = f"""
Siz web sahifa muammo tahlilchisisiz.
Sahifa URL: {page_url}
Kutilgan harakat: {expected_action}

Harakat bajarildi lekin sahifa o'zgarmadi. Nima muammo bo'lishi mumkin?

FAQAT JSON qaytaring:
{{
    "problem_type": "validation_error|permission|not_found|ui_blocked|wrong_element|page_loading|other",
    "problem_description": "muammo tavsifi",
    "visible_errors": ["sahifada ko'ringan xato xabarlari"],
    "suggestion": "qanday harakat qilish kerak",
    "retry_possible": true/false
}}
"""
    text, token_info = _call_gemini(
        [system, {"mime_type": "image/png", "data": image_data}],
        step_name="analyze_stuck"
    )
    result = _extract_json(text)
    result["_token_info"] = token_info

    print(f"\n  ┌─ [DEBUG: STUCK SAHIFA TAHLILI] ───────────────────")
    print(f"  │ Muammo turi  : {result.get('problem_type')}")
    print(f"  │ Tavsif       : {result.get('problem_description')}")
    print(f"  │ Ko'ringan xato: {result.get('visible_errors')}")
    print(f"  │ Tavsiya      : {result.get('suggestion')}")
    print(f"  └───────────────────────────────────────────────────")

    return result
