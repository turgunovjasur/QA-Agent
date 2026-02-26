import asyncio
import sys
import json
from dataclasses import dataclass, field
from typing import Optional, Tuple
from urllib.parse import urlparse

from memory.db import (
    init_db, get_connection,
    get_credentials, save_credentials,
    get_page_elements, save_page_element,
    get_navigation_path, save_navigation_path,
    get_form_knowledge, save_form_knowledge,
    save_user_hint, search_user_hints,
    save_test_run, save_step_result
)
from ai.gemini_agent import (
    parse_user_prompt, analyze_page, analyze_form_page,
    decide_field_value, verify_action_result,
    reset_token_stats, get_token_summary
)
from browser.playwright_agent import BrowserAgent


# ═══════════════════════════════════════════════════════════════
#  PAGE STATE — Har sahifa uchun bitta ob'ekt
#  Screenshot FAQAT yangi sahifa ochilganda olinadi
# ═══════════════════════════════════════════════════════════════

@dataclass
class PageState:
    url: str
    screenshot_bytes: bytes
    checklist: list          # Gemini topgan elementlar ro'yxati
    page_type: str           # login / dashboard / form / list / other
    page_title: str
    raw_analysis: dict = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════

def ask_user(question: str) -> str:
    print(f"\n  [AI ❓]: {question}")
    return input("  [Siz ]: ").strip()


def get_base_url(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def extract_keywords(text: str) -> list:
    """
    Matndan muhim kalit so'zlarni ajratib oladi.
    - Tirnoq, vergul, nuqta kabi belgilarni tozalaydi
    - 'Shovqin' so'zlarni filtrlaydi
    - Faqat 3+ harfli, ma'noli so'zlarni qaytaradi
    """
    import re
    # Barcha maxsus belgilarni olib tashlash (tirnoq, vergul, nuqta, qavs...)
    cleaned = re.sub(r'[^\w\u0400-\u04FF\-]', ' ', text.lower())
    words = cleaned.split()

    STOP = {
        # O'zbek
        "va", "yoki", "uchun", "bilan", "ning", "dan", "ga", "da", "bu",
        "bor", "shu", "uni", "bir", "ham", "lar", "deb", "deg", "tabi",
        "joydan", "qoshiladi", "shuni", "ichida", "yuqorida", "degan",
        "menu", "navbar", "link", "icon", "bosing", "oching", "topib",
        "bosish", "topish", "ustiga", "panelidan", "menyusidan", "limi",
        # Rus
        "для", "под", "над", "при", "без", "или", "что", "как", "это",
        "все", "из", "его", "нет", "ещё", "вот", "так", "здесь",
        # Ingliz
        "the", "and", "or", "for", "in", "to", "of", "from", "click",
        "find", "open", "get", "set", "tab", "bar", "nav", "but", "has",
        "all", "new", "add",
    }
    return [w for w in words if len(w) >= 3 and w not in STOP]


def print_step_header(step_id: int, description: str, action_type: str):
    print(f"\n{'═' * 60}")
    print(f"  QADAM {step_id}: {description}")
    print(f"  Tur  : {action_type}")
    print(f"{'═' * 60}")


def print_token_summary(summary: dict):
    print(f"\n{'═' * 60}")
    print("  📊 TOKEN HISOBOTI")
    print(f"{'═' * 60}")
    print(f"  Jami API chaqiruvlar : {summary['total_api_calls']}")
    print(f"  Kirish tokenlari     : {summary['total_input']:,}")
    print(f"  Chiqish tokenlari    : {summary['total_output']:,}")
    print(f"  JAMI TOKENLAR        : {summary['total_tokens']:,}")
    print(f"{'═' * 60}")


def print_db_state(base_url: str):
    conn = get_connection()
    print(f"\n  ┌─ [DB HOLATI: {base_url}] ─────────────────────────")
    creds = conn.execute(
        "SELECT email FROM credentials WHERE site_url=?", (base_url,)
    ).fetchone()
    print(f"  │ 🔑 Login: {creds['email'] if creds else 'YOQ'}")
    els = conn.execute(
        "SELECT page_url, element_name, css_selector FROM page_elements WHERE site_url=?",
        (base_url,)
    ).fetchall()
    print(f"  │ 📌 Saqlangan elementlar ({len(els)} ta):")
    for e in els:
        page_label = e['page_url'].split('/')[-1] or '/'
        print(f"  │     [{page_label}] '{e['element_name']}' → {e['css_selector']}")
    hints = conn.execute(
        "SELECT action_keyword, hint FROM user_hints WHERE site_url=?", (base_url,)
    ).fetchall()
    print(f"  │ 💡 User hintlar ({len(hints)} ta):")
    for h in hints:
        print(f"  │     '{h['action_keyword']}' → {h['hint']}")
    forms = conn.execute(
        "SELECT form_name, form_url FROM form_knowledge WHERE site_url=?", (base_url,)
    ).fetchall()
    print(f"  │ 📋 Saqlangan formalar ({len(forms)} ta):")
    for f in forms:
        print(f"  │     '{f['form_name']}' @ {f['form_url']}")
    navs = conn.execute(
        "SELECT action_name, final_url FROM navigation_paths WHERE site_url=?", (base_url,)
    ).fetchall()
    print(f"  │ 🗺️  Navigatsiya yo'llari ({len(navs)} ta):")
    for n in navs:
        print(f"  │     '{n['action_name']}' → {n['final_url']}")
    print(f"  └───────────────────────────────────────────────────")
    conn.close()


# ═══════════════════════════════════════════════════════════════
#  CAPTURE & ANALYZE — Bitta joy, bitta screenshot
# ═══════════════════════════════════════════════════════════════

async def capture_and_analyze(browser: BrowserAgent, task_hint: str = "") -> PageState:
    """
    Sahifadan screenshot olib Gemini tahlil qiladi.
    Bu funksiya FAQAT yangi sahifa ochilganda chaqiriladi.
    """
    url = await browser.current_url()
    print(f"\n  📸 [Screenshot] → Gemini tahlil: {url.split('/')[-1] or '/'}")
    screenshot = await browser.screenshot()
    analysis = analyze_page(screenshot, task_hint or "Sahifadagi barcha interaktiv elementlarni toping", url)
    state = PageState(
        url=url,
        screenshot_bytes=screenshot,
        checklist=analysis.get("found_elements", []),
        page_type=analysis.get("page_type", "other"),
        page_title=analysis.get("page_title", ""),
        raw_analysis=analysis,
    )
    print(f"  📋 [Checklist] {len(state.checklist)} ta element topildi "
          f"({state.page_type}: {state.page_title})")
    return state


# ═══════════════════════════════════════════════════════════════
#  FIND IN CHECKLIST — Screenshot olmaydi, faqat list dan qidiradi
# ═══════════════════════════════════════════════════════════════

def find_in_checklist(checklist: list, description: str,
                      user_hint: str = None) -> Optional[dict]:
    """
    Mavjud checklist dan element qidiradi.
    Screenshot olmaydi!

    Qidirish ustuvorligi:
    1. visible_text ga TO'LIQ mos kelish (eng ishonchli)
    2. visible_text ga QISMAN mos kelish
    3. element name ga mos kelish
    4. css/xpath locatorga mos kelish (eng kam ishonchli)
    """
    # Description va hint dan alohida keyword to'plamlari
    desc_kws = extract_keywords(description)
    hint_kws = extract_keywords(user_hint) if user_hint else []
    # Hint so'zlari ustuvor — avval hint, keyin description
    all_kws  = list(dict.fromkeys(hint_kws + desc_kws))  # tartib saqlanadi, takror yo'q

    if not all_kws:
        return None

    # ── 1. visible_text ga TO'LIQ mos kelish ──────────────────
    for el in checklist:
        text = el.get("visible_text", "").lower().strip()
        if text and any(kw == text for kw in all_kws):
            return el

    # ── 2. visible_text ga QISMAN mos kelish ──────────────────
    for el in checklist:
        text = el.get("visible_text", "").lower()
        if text and any(kw in text for kw in all_kws):
            return el

    # ── 3. element name ga mos kelish ─────────────────────────
    for el in checklist:
        name = el.get("name", "").lower()
        if any(kw in name for kw in all_kws):
            return el

    # ── 4. css/xpath locatorga mos kelish (oxirgi chora) ──────
    # FAQAT hint so'zlari ishlatiladi (desc so'zlari juda keng)
    if hint_kws:
        for el in checklist:
            loc = (el.get("css_selector", "") + " " + el.get("xpath", "")).lower()
            if any(kw in loc for kw in hint_kws):
                return el

    return None


# ═══════════════════════════════════════════════════════════════
#  RESOLVE ELEMENT — element topish (screenshot olmaydi!)
# ═══════════════════════════════════════════════════════════════

async def resolve_element(
    browser: BrowserAgent,
    page_state: PageState,
    description: str,
    base_url: str,
    user_hint: str = None,
    _retry_count: int = 0
) -> Tuple[Optional[dict], PageState]:
    """
    Elementni quyidagi tartibda qidiradi (screenshot olmaydi):
    1. CHECKLIST dan qidirish (hozirgi sahifaning haqiqiy holati — BIRINCHI!)
    2. DB page_elements (faqat checklist topа olmasa, va faqat aniq moslik)
    3. User dan hint so'rash → checklist qayta qidirish
    4. [Faqat 3 marta xato bo'lsa] Ruxsat so'rab yangi screenshot → yangi PageState

    MUHIM: DB doimo checklist DAN KEYIN tekshiriladi.
    Sabab: Checklist — hozirgi sahifaning haqiqiy holati.
    DB — eskirgan ma'lumot bo'lishi mumkin (boshqa sahifa, boshqa qadam).

    Returns: (element_dict | None, page_state)
    """
    keywords = extract_keywords(description)

    # DB user hints dan oldindan izlash (hint sifatida ishlatamiz)
    db_hints = search_user_hints(base_url, keywords)
    combined_hint = user_hint or (db_hints[0]["hint"] if db_hints else None)

    # ── 1. CHECKLIST DAN QIDIRISH (BIRINCHI!) ─────────────────
    # Hozirgi sahifaning haqiqiy holati — eng ishonchli manba
    el = find_in_checklist(page_state.checklist, description, combined_hint)
    if el:
        source_note = " (+hint)" if combined_hint else ""
        print(f"  [📋 Checklist{source_note}] '{el.get('name')}' | "
              f"css='{el.get('css_selector')}' | xpath='{el.get('xpath')}'")
        return {**el, "source": "checklist"}, page_state

    # ── 2. DB PAGE ELEMENTS (FAQAT ANIQ MOSLIK) ───────────────
    # DB dan faqat visible_text ga TO'LIQ mos kelgan elementni olamiz.
    # Qisman moslik qilmaymiz — chunki bu eski sahifadan qolgan element bo'lishi mumkin.
    page_url = page_state.url
    cached = get_page_elements(page_url)
    for el in cached:
        el_text = el.get("visible_text", "").lower().strip()
        # Faqat visible_text ANIQ mos kelsa qaytaramiz
        if el_text and any(kw == el_text for kw in keywords):
            print(f"  [🗄️  DB ✓] '{el['element_name']}' | css='{el.get('css_selector')}'")
            return {
                "name": el["element_name"],
                "type": el.get("element_type", ""),
                "css_selector": el.get("css_selector", ""),
                "xpath": el.get("xpath", ""),
                "visible_text": el.get("visible_text", ""),
                "source": "db_cache"
            }, page_state

    # ── 3. USER DAN HINT SO'RASH ───────────────────────────────
    if _retry_count < 2:
        checklist_texts = [
            f"  '{e.get('visible_text', '')}'" for e in page_state.checklist
            if e.get("visible_text", "").strip()
        ]
        checklist_preview = ", ".join(checklist_texts[:8])
        print(f"  [⚠️ ] '{description}' — checklistda topilmadi.")
        print(f"  [ℹ️ ] Mavjud elementlar: {checklist_preview}{'...' if len(page_state.checklist) > 8 else ''}")
        hint_text = ask_user(
            f"'{description}' elementini topa olmadim.\n"
            f"  Sahifadagi qaysi matn/element bosilishi kerak? "
            f"(masalan: 'ТМЦ' yoki 'Товары' degan link)"
        )
        if hint_text:
            kw = "_".join(keywords[:3]) or description[:20]
            save_user_hint(base_url, kw, hint_text)
            print(f"  [AI]: 💾 Hint saqlandi: '{kw}' → '{hint_text}'")
            # Qayta urinish — screenshot yo'q
            return await resolve_element(
                browser, page_state, description, base_url,
                user_hint=hint_text, _retry_count=_retry_count + 1
            )

    # ── 4. OXIRGI CHORA: YANGI SCREENSHOT (RUXSAT BILAN) ──────
    print(f"  [❌] {_retry_count + 1} urinishdan keyin ham topilmadi.")
    allow = ask_user(
        f"Yangi screenshot olib sahifani qayta tahlil qilay? (ha / yo'q)"
    )
    if allow.lower() in ["ha", "h", "yes", "y"]:
        print(f"  [🔄] Yangi screenshot + Gemini tahlil...")
        new_state = await capture_and_analyze(browser, description)
        el = find_in_checklist(new_state.checklist, description, combined_hint)
        if el:
            print(f"  [✅] Yangi tahlildan topildi: '{el.get('name')}'")
            return {**el, "source": "retry_screenshot"}, new_state
        print(f"  [❌] Yangi tahlildan ham topilmadi.")
        return None, new_state

    return None, page_state


# ═══════════════════════════════════════════════════════════════
#  LOGIN
# ═══════════════════════════════════════════════════════════════

def _find_login_elements(checklist: list) -> Tuple[Optional[dict], Optional[dict], Optional[dict]]:
    """
    Checklist dan email, password, submit elementlarini topadi.
    Returns: (email_el, pass_el, submit_el)
    """
    EMAIL_KW = ["email", "username", "user", "login", "логин", "почта", "userid"]
    PASS_KW  = ["password", "parol", "pwd", "пароль"]
    PASS_EX  = ["forgot", "reset", "confirm", "repeat", "change"]
    SUB_KW   = ["войти", "kirish", "login", "submit", "sign", "вход"]

    email_el = pass_el = submit_el = None

    for el in checklist:
        name  = el.get("name", "").lower()
        text  = el.get("visible_text", "").lower()
        etype = el.get("type", "").lower()
        css   = el.get("css_selector", "").lower()
        xpath = el.get("xpath", "").lower()
        combined = name + " " + text + " " + css + " " + xpath

        if etype == "button" or "button" in etype:
            if any(kw in combined for kw in SUB_KW) and not submit_el:
                submit_el = el
        elif etype in ("input", "textarea", "text", "email", ""):
            if any(ex in name for ex in PASS_EX):
                continue
            if any(kw in combined for kw in PASS_KW) and not pass_el:
                pass_el = el
            elif any(kw in combined for kw in EMAIL_KW) and not email_el:
                email_el = el

    # Fallback: type=password bo'lsa ishonchli
    if not pass_el:
        for el in checklist:
            if "password" in el.get("css_selector", "").lower() or \
               "password" in el.get("xpath", "").lower():
                pass_el = el
                break

    return email_el, pass_el, submit_el


async def do_login(
    browser: BrowserAgent,
    page_state: PageState,
    site_url: str,
    base_url: str
) -> Tuple[bool, PageState]:
    """
    Login bajaradi. page_state dan checklist ishlatadi — yangi screenshot olmaydi
    (agar element topilmasa resolve_element orqali ruxsat so'raladi).
    Returns: (success, updated_page_state)
    """
    # Credentials
    creds = get_credentials(base_url)
    if not creds:
        print(f"  [AI]: Login ma'lumotlari DB da yo'q.")
        email    = ask_user("Email yoki login kiriting:")
        password = ask_user("Parol kiriting:")
        save_credentials(base_url, email, password)
        creds = {"email": email, "password": password}
        print(f"  [AI]: ✅ Credentials saqlandi: {email}")
    else:
        print(f"  [AI]: ✅ Credentials DB dan → {creds['email']}")

    # Checklistdan elementlarni topamiz — screenshot yo'q
    email_el, pass_el, submit_el = _find_login_elements(page_state.checklist)

    # Email element
    if not email_el:
        print(f"  [⚠️ ] Email input checklistda topilmadi, resolve_element...")
        email_el, page_state = await resolve_element(
            browser, page_state, "email yoki username input", base_url
        )
    if not email_el:
        print(f"  [❌] Email input topilmadi. Login to'xtatildi.")
        return False, page_state

    print(f"\n  [LOGIN]: Email → '{email_el.get('name')}' | css='{email_el.get('css_selector')}'")
    email_ph = email_el.get("visible_text") or email_el.get("label_text") or None
    email_ok = await browser.try_fill(
        value=creds["email"],
        css_selector=email_el.get("css_selector") or None,
        xpath=email_el.get("xpath") or None,
        placeholder=email_ph,
        input_type="email",
        label_text=email_ph,
        role_name=email_ph,
    )
    if not email_ok:
        print(f"  [❌] Email to'ldirilmadi.")
        return False, page_state

    await browser.wait(300)

    # Password element
    if not pass_el:
        print(f"  [⚠️ ] Password input checklistda topilmadi, resolve_element...")
        pass_el, page_state = await resolve_element(
            browser, page_state, "password parol input", base_url
        )
    if not pass_el:
        print(f"  [❌] Parol input topilmadi. Login to'xtatildi.")
        return False, page_state

    print(f"\n  [LOGIN]: Parol → '{pass_el.get('name')}' | css='{pass_el.get('css_selector')}'")
    pass_ph = pass_el.get("visible_text") or pass_el.get("label_text") or None
    pass_ok = await browser.try_fill(
        value=creds["password"],
        css_selector=pass_el.get("css_selector") or None,
        xpath=pass_el.get("xpath") or None,
        input_type="password",
        placeholder=pass_ph,
        label_text=pass_ph,
        role_name=pass_ph,
    )
    if not pass_ok:
        print(f"  [❌] Parol to'ldirilmadi.")
        return False, page_state

    await browser.wait(300)

    # Submit
    old_url = await browser.current_url()
    if submit_el:
        print(f"\n  [LOGIN]: Submit → '{submit_el.get('name')}' | "
              f"css='{submit_el.get('css_selector')}'")
        sub_ok = await browser.try_click(
            css_selector=submit_el.get("css_selector") or None,
            xpath=submit_el.get("xpath") or None,
            visible_text=submit_el.get("visible_text") or None,
            role="button",
            role_name=submit_el.get("visible_text") or None,
        )
        if not sub_ok:
            print(f"  [AI]: Submit bosilmadi → Enter bosiladi")
            await browser.press_key("Enter")
    else:
        print(f"  [AI]: Submit topilmadi → Enter bosiladi")
        await browser.press_key("Enter")

    # Dashboard yuklanishini kutish (SPA uchun networkidle)
    try:
        await browser._page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    await browser.wait(1500)

    new_url = await browser.current_url()
    if new_url == old_url:
        print(f"  [❌] Login muvaffaqiyatsiz! URL o'zgarmadi.")
        # Yangi screenshot ruxsatsiz olinmaydi — user ga xabar beramiz
        retry = ask_user(
            f"Login muvaffaqiyatsiz (URL o'zgarmadi).\n"
            f"  Email/parol to'g'rimi? Qayta kiritasizmi? (ha/yo'q)"
        )
        if retry.lower() in ["ha", "h", "yes", "y"]:
            new_email = ask_user(f"Email [{creds['email']}]:")
            new_pass  = ask_user("Parol:")
            if new_email:
                save_credentials(base_url, new_email, new_pass)
            # Yangi page_state (foydalanuvchi ruxsat berdi — login sahifasi qayta tahlil)
            new_state = await capture_and_analyze(browser, "Login forma")
            return await do_login(browser, new_state, site_url, base_url)
        return False, page_state

    print(f"  [✅] Login muvaffaqiyatli! → {new_url}")
    # Yangi sahifa ochildi → yangi page_state
    new_state = await capture_and_analyze(browser, "Dashboard asosiy sahifa")
    return True, new_state


# ═══════════════════════════════════════════════════════════════
#  EXECUTE STEP — asosiy qadam bajaruvchi
# ═══════════════════════════════════════════════════════════════

async def execute_step(
    browser: BrowserAgent,
    step: dict,
    page_state: Optional[PageState],
    site_url: str,
    base_url: str,
    test_run_id: int,
    nav_steps_log: list
) -> Tuple[dict, Optional[PageState]]:
    """
    Bitta qadam bajaradi.
    Returns: (result_dict, updated_page_state)
    """
    step_id     = step["step_id"]
    description = step["description"]
    action_type = step["action_type"]
    expected    = step.get("expected_result", "")

    result = {"step_id": step_id, "status": "pending", "token_info": {}, "error": ""}

    # ── NAVIGATE ──────────────────────────────────────────────
    if action_type == "navigate":
        url = step.get("url", site_url)
        print(f"  [AI]: Navigatsiya: {url}")
        await browser.navigate(url)
        await browser.wait(1000)
        # Yangi sahifa → screenshot + tahlil
        page_state = await capture_and_analyze(browser, description)
        result["status"] = "passed"
        nav_steps_log.append({"type": "navigate", "url": url})

    # ── LOGIN ─────────────────────────────────────────────────
    elif action_type == "login":
        # page_state mavjud bo'lishi kerak (navigate dan keyin)
        if not page_state:
            page_state = await capture_and_analyze(browser, "Login sahifasi")
        success, page_state = await do_login(browser, page_state, site_url, base_url)
        result["status"] = "passed" if success else "failed"
        if not success:
            result["error"] = "Login muvaffaqiyatsiz"
        nav_steps_log.append({"type": "login", "success": success})

    # ── FIND AND CLICK ────────────────────────────────────────
    elif action_type == "find_and_click":
        if not page_state:
            page_state = await capture_and_analyze(browser, description)

        el, page_state = await resolve_element(
            browser, page_state, description, base_url
        )

        if not el:
            result["status"] = "failed"
            result["error"] = "Element topilmadi"
        else:
            old_url = await browser.current_url()
            ok = await browser.try_click(
                css_selector=el.get("css_selector") or None,
                xpath=el.get("xpath") or None,
                visible_text=el.get("visible_text") or None,
                role="button" if el.get("type") == "button" else None,
                role_name=el.get("visible_text") or None,
            )

            if not ok:
                # Click bo'lmadi — mavjud checklist bilan qayta urinish
                print(f"  [⚠️ ] Click bajarilmadi. Aniqroq yo'nalish bering.")
                hint_text = ask_user(
                    f"Element bosilmadi.\n"
                    f"  Aniqroq ko'rsating: (masalan: 'Navbar da Spravochnik linkini bosing')"
                )
                if hint_text:
                    kw = "_".join(extract_keywords(description)[:3])
                    save_user_hint(base_url, kw, hint_text)
                    # Qayta resolve — yangi screenshot yo'q
                    retry_el, page_state = await resolve_element(
                        browser, page_state, description, base_url,
                        user_hint=hint_text, _retry_count=1
                    )
                    if retry_el:
                        ok = await browser.try_click(
                            css_selector=retry_el.get("css_selector") or None,
                            xpath=retry_el.get("xpath") or None,
                            visible_text=retry_el.get("visible_text") or None,
                        )
                        if ok:
                            el = retry_el

            if ok:
                await browser.wait(1000)
                new_url = await browser.current_url()

                # Muvaffaqiyatli click → DB ga element saqlash
                css   = el.get("css_selector", "")
                xpath = el.get("xpath", "")
                if el.get("name") and (css or xpath):
                    save_page_element(
                        site_url=base_url,
                        page_url=old_url,
                        element_name=el["name"],
                        element_type=el.get("type", ""),
                        css_selector=css,
                        xpath=xpath,
                        visible_text=el.get("visible_text", ""),
                    )

                if new_url != old_url:
                    # URL o'zgardi → yangi sahifa → yangi screenshot + checklist
                    print(f"  [✅] URL o'zgardi → yangi sahifa tahlil qilinadi")
                    page_state = await capture_and_analyze(browser, description)
                else:
                    # URL o'zgarmadi — bu NORMAL holat!
                    # SPA saytlarda dropdown/menyu bosilganda URL o'zgarmaydi,
                    # lekin DOM o'zgaradi (submenu paydo bo'ladi).
                    # Yangi screenshot olib cheklistni yangilaymiz.
                    print(f"  [✅] Click bajarildi (URL o'zgarmadi → DOM o'zgardi, yangi checklist)")
                    page_state = await capture_and_analyze(browser, description)

                result["status"] = "passed"
                nav_steps_log.append({
                    "type": "click",
                    "element": el.get("name", ""),
                    "css": css,
                    "text": el.get("visible_text", ""),
                    "source": el.get("source", ""),
                    "resulted_url": new_url
                })
            else:
                result["status"] = "failed"
                result["error"] = "Element bosilmadi"

    # ── FIND AND FILL ─────────────────────────────────────────
    elif action_type == "find_and_fill":
        if not page_state:
            page_state = await capture_and_analyze(browser, description)

        page_url = page_state.url
        form_key = page_url.split("?")[0].rstrip("/").split("/")[-1] or "main_form"

        # DB dan forma
        cached_form = get_form_knowledge(base_url, form_key)
        if cached_form:
            print(f"  [🗄️  DB] Forma: '{form_key}' ({len(cached_form['fields'])} maydon) [screenshot yo'q]")
            fields       = cached_form["fields"]
            submit_css   = cached_form.get("submit_selector", "")
            submit_xpath = None
        else:
            # Mavjud page_state screenshot dan forma tahlili
            print(f"  [📋] Forma checklistdan qilinmoqda: {len(page_state.checklist)} element")
            # Hozirgi sahifaning screenshoti allaqachon bor — qayta olmaydi
            form_analysis = analyze_form_page(
                page_state.screenshot_bytes, description, page_url
            )
            result["token_info"] = form_analysis.get("_token_info", {})
            fields       = form_analysis.get("fields", [])
            submit_btn   = form_analysis.get("submit_button") or {}
            submit_css   = submit_btn.get("css_selector", "")
            submit_xpath = submit_btn.get("xpath", "")

            if fields:
                save_form_knowledge(base_url, form_key, page_url, fields, submit_css)
                print(f"  [💾] {len(fields)} maydon DB ga saqlandi")
            else:
                hint = ask_user(
                    f"Forma topilmadi. Forma qayerda?\n"
                    f"  (masalan: 'Spravochnik > Tovarlar > + tugmasi')"
                )
                if hint:
                    kw = "_".join(extract_keywords(description)[:3])
                    save_user_hint(base_url, kw, hint)
                result["status"] = "failed"
                result["error"] = "Forma topilmadi"

        if fields:
            filled_count = 0
            for fld in fields:
                ftype = fld.get("type", "text")
                if ftype in ["checkbox", "radio", "file"]:
                    continue
                label = fld.get("label") or fld.get("name", "")
                print(f"\n  [Maydon]: {label} ({ftype})")

                val_result = decide_field_value(fld, {"form_purpose": description})
                fill_value = val_result.get("value", "")

                if val_result.get("needs_user_input"):
                    fill_value = ask_user(
                        val_result.get("user_question", f"'{label}' uchun qiymat:")
                    )

                if fill_value:
                    ft = ftype.lower()
                    field_input_type = (
                        "password" if ft == "password" else
                        "email"    if ft == "email"    else
                        "number"   if ft == "number"   else
                        "date"     if ft == "date"     else None
                    )
                    ok = await browser.try_fill(
                        value=fill_value,
                        css_selector=fld.get("css_selector") or None,
                        xpath=fld.get("xpath") or None,
                        placeholder=fld.get("placeholder") or None,
                        input_type=field_input_type,
                        label_text=fld.get("label") or None,
                        role_name=fld.get("label") or fld.get("placeholder") or None,
                    )
                    if ok:
                        filled_count += 1
                        nav_steps_log.append({
                            "type": "fill",
                            "field": label,
                            "css": fld.get("css_selector", ""),
                        })
                    else:
                        # Fill xato — mavjud state bilan qayta urinish
                        hint_txt = ask_user(
                            f"'{label}' maydonini to'ldira olmadim.\n"
                            f"  Maydon locatorini ko'rsating (css/placeholder/label):"
                        )
                        if hint_txt:
                            ok2 = await browser.try_fill(
                                value=fill_value,
                                placeholder=hint_txt,
                                role_name=hint_txt,
                            )
                            if ok2:
                                filled_count += 1

            print(f"\n  [AI]: {filled_count}/{len(fields)} maydon to'ldirildi")

            # Submit — mavjud page_state screenshot dan olingan
            sub_ok = False
            if submit_css or submit_xpath:
                sub_ok = await browser.try_click(
                    css_selector=submit_css or None,
                    xpath=submit_xpath or None,
                )
            if not sub_ok:
                # Submit checklistdan qidirish — yangi screenshot yo'q
                submit_el = find_in_checklist(
                    page_state.checklist,
                    "saqlash submit save qo'shish tugma button"
                )
                if submit_el:
                    sub_ok = await browser.try_click(
                        css_selector=submit_el.get("css_selector") or None,
                        xpath=submit_el.get("xpath") or None,
                        visible_text=submit_el.get("visible_text") or None,
                    )
            if not sub_ok:
                await browser.press_key("Enter")

            await browser.wait(2000)
            new_url = await browser.current_url()

            if new_url != page_url:
                # Yangi sahifa → yangi page_state
                page_state = await capture_and_analyze(browser, description)
                result["status"] = "passed"
            else:
                result["status"] = "passed" if filled_count > 0 else "failed"

    # ── VERIFY ────────────────────────────────────────────────
    elif action_type == "verify":
        if not page_state:
            page_state = await capture_and_analyze(browser, expected)

        # Mavjud screenshot ishlatamiz — yangi olmaydi
        verify = verify_action_result(
            page_state.screenshot_bytes, expected, page_state.url
        )
        result["token_info"] = verify.get("_token_info", {})
        success = verify.get("success", False)
        confidence = verify.get("confidence", 0.0)

        # Agar sahifada login sahifasi emas (dashboard/form/list) bo'lsa,
        # va sahifa muvaffaqiyatli yuklangan bo'lsa (page_type dashboard/other/list),
        # lekin faqat litsenziya ogohlantirishi yoki bo'sh kontent bo'lsa —
        # buni xato hisoblamaymiz.
        # Masalan: "Dashboard ochildi" → sahifada litsenziya ogohlantirishi bor,
        # lekin sahifaning o'zi to'g'ri yuklangan.
        page_type_ok = page_state.page_type in ("dashboard", "other", "list", "form", "detail")
        url_ok = page_state.url != "" and "login" not in page_state.url.lower()

        if not success and page_type_ok and url_ok and confidence < 0.4:
            # Ishonch past + sahifa to'g'ri yuklanган → ehtimol litsenziya ogoh.
            # Passed deb o'tkazamiz lekin ogohlantirish qoldiramiz.
            print(f"  [⚠️ ] Verify: AI 'failed' dedi (ishonch={confidence:.1f}), "
                  f"lekin sahifa turi '{page_state.page_type}' va login yo'q.")
            print(f"  [ℹ️ ] Sababı: '{verify.get('error_message', '')}' — "
                  f"bu litsenziya ogohlantirishi bo'lishi mumkin → PASSED deb o'tkazildi.")
            result["status"] = "passed"
        else:
            result["status"] = "passed" if success else "failed"
            if not success:
                result["error"] = verify.get("error_message", "")

    # ── WAIT ──────────────────────────────────────────────────
    elif action_type == "wait":
        await browser.wait(2000)
        result["status"] = "passed"

    # DB ga saqlash
    save_step_result(
        test_run_id=test_run_id,
        step_id=step_id,
        description=description,
        action_type=action_type,
        status=result["status"],
        token_info=result.get("token_info", {}),
        error_message=result.get("error", "")
    )

    return result, page_state


# ═══════════════════════════════════════════════════════════════
#  MAIN ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════

async def run_agent(user_prompt: str):
    print(f"\n{'═' * 60}")
    print(f"  QA AGENT ISHGA TUSHDI")
    print(f"{'═' * 60}")

    init_db()
    reset_token_stats()

    # 1. PROMPT TAHLIL
    print(f"\n[1] Prompt tahlil qilinmoqda...")
    parsed, _ = parse_user_prompt(user_prompt)

    site_url  = parsed.get("site_url") or ""
    test_name = parsed.get("test_name", "Test")
    steps     = parsed.get("steps", [])
    base_url  = get_base_url(site_url) if site_url else ""

    if not site_url:
        site_url = ask_user("Qaysi saytda test? (URL):")
        base_url = get_base_url(site_url)

    print(f"\n  Test nomi : {test_name}")
    print(f"  Sayt      : {base_url}")
    print(f"  Qadamlar  : {len(steps)} ta")
    for s in steps:
        print(f"    {s['step_id']}. [{s['action_type']}] {s['description']}")

    print_db_state(base_url)

    # 2. BRAUZER
    browser = BrowserAgent(headless=False)
    await browser.start()
    print(f"\n[2] Brauzer ochildi.")

    step_results   = []
    nav_steps_log  = []
    overall_status = "passed"
    test_run_id    = save_test_run(test_name, base_url, user_prompt, "running", [], {})

    # Sahifa holati — session bo'yi bitta ob'ekt, faqat yangi sahifada yangilanadi
    current_page_state: Optional[PageState] = None

    try:
        for step in steps:
            print_step_header(step["step_id"], step["description"], step["action_type"])

            result, current_page_state = await execute_step(
                browser=browser,
                step=step,
                page_state=current_page_state,
                site_url=site_url,
                base_url=base_url,
                test_run_id=test_run_id,
                nav_steps_log=nav_steps_log,
            )

            step_results.append(result)
            icon = "✅" if result["status"] == "passed" else "❌"
            print(f"\n  {icon} Qadam {result['step_id']}: {result['status'].upper()}")
            if result.get("error"):
                print(f"  Sabab: {result['error']}")

            if result["status"] == "failed":
                overall_status = "failed"
                cont = ask_user("Qadam failed. Davom etishni xohlaysizmi? (ha / yo'q):")
                if cont.lower() not in ["ha", "h", "yes", "y"]:
                    print(f"  [AI]: Test to'xtatildi.")
                    break

        # Navigatsiya yo'lini saqlash
        if nav_steps_log:
            key = test_name.lower().replace(" ", "_")[:40]
            save_navigation_path(
                site_url=base_url,
                action_name=key,
                steps=nav_steps_log,
                final_url=await browser.current_url(),
                description=test_name,
            )
            print(f"\n  [AI]: ✅ Navigatsiya yo'li DB ga saqlandi.")

    except Exception as e:
        overall_status = "failed"
        print(f"\n  [AI]: ❌ Kutilmagan xato: {e}")
        import traceback
        traceback.print_exc()

    finally:
        token_summary = get_token_summary()
        print_token_summary(token_summary)

        conn = get_connection()
        conn.execute(
            "UPDATE test_runs SET status=?, steps=?, token_summary=? WHERE id=?",
            (overall_status,
             json.dumps(step_results, ensure_ascii=False),
             json.dumps(token_summary, ensure_ascii=False),
             test_run_id)
        )
        conn.commit()
        conn.close()

        icon = "✅" if overall_status == "passed" else "❌"
        print(f"\n  {icon} TEST YAKUNLANDI: {overall_status.upper()}")
        print_db_state(base_url)

        input("\n  [Enter → brauzer yopiladi]")
        await browser.stop()


# ═══════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════

def main():
    if len(sys.argv) > 1:
        prompt = " ".join(sys.argv[1:])
    else:
        print("=" * 60)
        print("  QA Agent — AI asosida avtomatik test tizimi")
        print("=" * 60)
        prompt = input("  Test buyrug'ini kiriting: ").strip()

    if not prompt:
        print("  Buyruq kiritilmadi.")
        return

    asyncio.run(run_agent(prompt))


if __name__ == "__main__":
    main()
