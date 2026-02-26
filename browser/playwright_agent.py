from playwright.async_api import async_playwright, Page, Browser


class BrowserAgent:
    def __init__(self, headless: bool = False):
        self.headless = headless
        self._playwright = None
        self._browser: Browser = None
        self._page: Page = None

    async def start(self):
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self.headless)
        context = await self._browser.new_context(
            viewport={"width": 1366, "height": 768}
        )
        self._page = await context.new_page()

    async def stop(self):
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def navigate(self, url: str):
        await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await self._page.wait_for_timeout(1500)

    async def screenshot(self) -> bytes:
        return await self._page.screenshot(full_page=False)

    async def current_url(self) -> str:
        return self._page.url

    async def get_page_title(self) -> str:
        return await self._page.title()

    async def wait(self, ms: int = 1000):
        await self._page.wait_for_timeout(ms)

    async def press_key(self, key: str):
        await self._page.keyboard.press(key)
        await self._page.wait_for_timeout(300)

    async def url_changed(self, old_url: str) -> bool:
        return self._page.url != old_url

    # ═══════════════════════════════════════════════════════════
    #  SMART FILL — faqat haqiqiy input/textarea elementlarni
    # ═══════════════════════════════════════════════════════════

    async def try_fill(
        self,
        value: str,
        css_selector: str = None,
        xpath: str = None,
        label_text: str = None,
        placeholder: str = None,
        input_type: str = None,      # 'password', 'email', 'text', ...
        role_name: str = None,       # get_by_role("textbox", name=...)
    ) -> bool:
        """
        Playwright ning barcha smart locatorlarini ketma-ket sinab ko'radi.
        Faqat haqiqiy input/textarea elementni to'ldiradi.
        Muvaffaqiyatli bo'lsa True, aks holda False qaytaradi.
        """
        print(f"\n  ┌─ [PLAYWRIGHT: try_fill] ───────────────────────────")
        print(f"  │ Qiymat      : '{value}'")
        print(f"  │ placeholder : {placeholder}")
        print(f"  │ input_type  : {input_type}")
        print(f"  │ role_name   : {role_name}")
        print(f"  │ label_text  : {label_text}")
        print(f"  │ css         : {css_selector}")
        print(f"  │ xpath       : {xpath}")

        strategies = []

        # 1. get_by_placeholder — eng ishonchli (placeholder bo'lsa)
        if placeholder:
            strategies.append(("placeholder", placeholder))

        # 2. get_by_role("textbox", name=...) — ARIA role + matn
        if role_name:
            strategies.append(("role_textbox", role_name))

        # 3. input[type=password/email] — type bo'yicha aniq
        if input_type:
            strategies.append(("input_type", f"input[type='{input_type}']"))

        # 4. get_by_label — label matniga qarab
        if label_text:
            strategies.append(("label", label_text))

        # 5. CSS selector — AI tahlildan kelgan
        if css_selector:
            strategies.append(("css", css_selector))

        # 6. XPath — AI tahlildan kelgan
        if xpath:
            strategies.append(("xpath", xpath))

        for strategy, loc_val in strategies:
            try:
                el = None

                if strategy == "placeholder":
                    el = self._page.get_by_placeholder(loc_val, exact=False).first
                elif strategy == "role_textbox":
                    el = self._page.get_by_role("textbox", name=loc_val).first
                elif strategy == "input_type":
                    el = self._page.locator(loc_val).first
                elif strategy == "label":
                    el = self._page.get_by_label(loc_val, exact=False).first
                elif strategy == "css":
                    el = self._page.locator(loc_val).first
                elif strategy == "xpath":
                    el = self._page.locator(f"xpath={loc_val}").first

                if el is None:
                    continue

                is_vis = await el.is_visible(timeout=2000)
                print(f"  │ [{strategy}] '{str(loc_val)[:50]}' → ko'rinadi={is_vis}")

                if not is_vis:
                    continue

                # Muhim: element haqiqatan input/textarea ekanligini tekshiramiz
                tag = await el.evaluate("el => el.tagName.toLowerCase()")
                el_type = await el.evaluate("el => el.type || ''")
                content_editable = await el.evaluate("el => el.isContentEditable")

                is_fillable = (
                    tag in ["input", "textarea"]
                    or content_editable
                )
                print(f"  │   tag={tag} type={el_type} editable={content_editable} → fillable={is_fillable}")

                if not is_fillable:
                    print(f"  │   ⚠️  Bu element input emas, o'tkazib yuborildi")
                    continue

                await el.scroll_into_view_if_needed()
                await el.fill(str(value), timeout=5000)
                await self._page.wait_for_timeout(300)
                print(f"  │ ✅ TO'LDIRILDI: '{value}' → [{strategy}] '{str(loc_val)[:50]}'")
                print(f"  └───────────────────────────────────────────────────")
                return True

            except Exception as ex:
                print(f"  │ ❌ [{strategy}] xato: {str(ex)[:100]}")
                continue

        print(f"  │ ❌ Hech bir locator ishlamadi!")
        print(f"  └───────────────────────────────────────────────────")
        return False

    # ═══════════════════════════════════════════════════════════
    #  SMART CLICK — button, link, va boshqa bosiladigan elementlar
    # ═══════════════════════════════════════════════════════════

    async def try_click(
        self,
        css_selector: str = None,
        xpath: str = None,
        visible_text: str = None,
        role: str = None,        # 'button', 'link', 'menuitem', ...
        role_name: str = None,   # get_by_role("button", name="Kirish")
    ) -> bool:
        """
        Playwright smart locatorlar bilan element topib bosadi.
        """
        print(f"\n  ┌─ [PLAYWRIGHT: try_click] ──────────────────────────")
        print(f"  │ role/name   : {role}/{role_name}")
        print(f"  │ visible_text: {visible_text}")
        print(f"  │ css         : {css_selector}")
        print(f"  │ xpath       : {xpath}")

        strategies = []

        # 1. get_by_role("button", name=...) — eng ishonchli
        if role and role_name:
            strategies.append(("role", (role, role_name)))

        # 2. Faqat role_name bilan (button default)
        if role_name and not role:
            strategies.append(("role", ("button", role_name)))

        # 3. Matn bo'yicha
        if visible_text:
            strategies.append(("text", visible_text))

        # 4. CSS
        if css_selector:
            strategies.append(("css", css_selector))

        # 5. XPath
        if xpath:
            strategies.append(("xpath", xpath))

        for strategy, loc_val in strategies:
            loc_str = str(loc_val)[:55]
            try:
                el = None

                if strategy == "role":
                    r, name = loc_val
                    el = self._page.get_by_role(r, name=name).first
                elif strategy == "text":
                    el = self._page.get_by_text(loc_val, exact=False).first
                elif strategy == "css":
                    el = self._page.locator(loc_val).first
                elif strategy == "xpath":
                    el = self._page.locator(f"xpath={loc_val}").first

                if el is None:
                    print(f"  │ [{strategy}] '{loc_str}' → el=None, o'tkazib yuborildi")
                    continue

                is_vis = await el.is_visible(timeout=2000)
                print(f"  │ [{strategy}] '{loc_str}' → ko'rinadi={is_vis}")

                if not is_vis:
                    # DOM da bor-yo'qligini ham tekshir
                    count = await self._page.locator(
                        f"xpath={loc_val}" if strategy == "xpath" else (
                            f"[role='{loc_val[0]}']" if strategy == "role" else str(loc_val)
                        )
                    ).count() if strategy not in ["role", "text"] else 0
                    print(f"  │   ⤷ Element ko'rinmaydi (sahifada topilgan soni: {count if strategy not in ['role','text'] else '?'})")
                    continue

                await el.scroll_into_view_if_needed()
                await el.click(timeout=5000)
                await self._page.wait_for_timeout(800)
                print(f"  │ ✅ BOSILDI: [{strategy}] '{loc_str}'")
                print(f"  └───────────────────────────────────────────────────")
                return True

            except Exception as ex:
                print(f"  │ ❌ [{strategy}] '{loc_str}' → XATO: {str(ex)[:120]}")
                continue

        print(f"  │ ❌ Hech bir locator ishlamadi! ({len(strategies)} ta strategiya sinaldi)")
        print(f"  └───────────────────────────────────────────────────")
        return False

    # ═══════════════════════════════════════════════════════════
    #  PAGE DOM — sahifaning barcha input/button elementlarini olish
    # ═══════════════════════════════════════════════════════════

    async def get_all_inputs(self) -> list:
        """
        Sahifadagi barcha ko'rinadigan input, textarea, select larni qaytaradi.
        Bu Gemini screenshot tahlilidan mustaqil - to'g'ridan DOM dan olinadi.
        """
        result = await self._page.evaluate("""
        () => {
            const inputs = [];
            const selectors = 'input:not([type=hidden]), textarea, select';
            document.querySelectorAll(selectors).forEach((el, i) => {
                const rect = el.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) {
                    inputs.push({
                        index: i,
                        tag: el.tagName.toLowerCase(),
                        type: el.type || '',
                        name: el.name || '',
                        id: el.id || '',
                        placeholder: el.placeholder || '',
                        value: el.value || '',
                        aria_label: el.getAttribute('aria-label') || '',
                        class: el.className || '',
                        visible: true
                    });
                }
            });
            return inputs;
        }
        """)
        return result

    async def get_all_buttons(self) -> list:
        """
        Sahifadagi barcha ko'rinadigan button va submit inputlarni qaytaradi.
        """
        result = await self._page.evaluate("""
        () => {
            const btns = [];
            document.querySelectorAll('button, input[type=submit], a[href], [role=button]').forEach((el, i) => {
                const rect = el.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) {
                    btns.push({
                        index: i,
                        tag: el.tagName.toLowerCase(),
                        type: el.type || '',
                        text: (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().substring(0, 80),
                        id: el.id || '',
                        name: el.name || '',
                        class: el.className || '',
                        href: el.href || ''
                    });
                }
            });
            return btns;
        }
        """)
        return result

    async def fill_by_dom_index(self, dom_index: int, value: str) -> bool:
        """DOM indeksi bo'yicha to'g'ridan input ga yozadi."""
        try:
            result = await self._page.evaluate(f"""
            () => {{
                const inputs = Array.from(
                    document.querySelectorAll('input:not([type=hidden]), textarea, select')
                ).filter(el => {{
                    const r = el.getBoundingClientRect();
                    return r.width > 0 && r.height > 0;
                }});
                const el = inputs[{dom_index}];
                if (el) {{
                    el.focus();
                    el.value = '';
                    return {{ found: true, tag: el.tagName, type: el.type, name: el.name }};
                }}
                return {{ found: false }};
            }}
            """)
            if not result.get("found"):
                return False

            # Playwright fill
            inputs = await self._page.query_selector_all(
                "input:not([type=hidden]), textarea, select"
            )
            visible_inputs = []
            for inp in inputs:
                box = await inp.bounding_box()
                if box and box["width"] > 0:
                    visible_inputs.append(inp)

            if dom_index < len(visible_inputs):
                el = visible_inputs[dom_index]
                await el.scroll_into_view_if_needed()
                await el.fill(str(value))
                await self._page.wait_for_timeout(300)
                print(f"  │ ✅ DOM[{dom_index}] to'ldirildi: '{value}' (tag={result.get('tag')} type={result.get('type')})")
                return True
        except Exception as ex:
            print(f"  │ ❌ DOM fill xato: {ex}")
        return False

    async def click_by_dom_index(self, dom_index: int) -> bool:
        """DOM indeksi bo'yicha button ni bosadi."""
        try:
            btns = await self._page.query_selector_all(
                "button, input[type=submit], [role=button]"
            )
            visible_btns = []
            for btn in btns:
                box = await btn.bounding_box()
                if box and box["width"] > 0:
                    visible_btns.append(btn)

            if dom_index < len(visible_btns):
                btn = visible_btns[dom_index]
                text = await btn.evaluate("el => el.innerText || el.value || ''")
                await btn.scroll_into_view_if_needed()
                await btn.click()
                await self._page.wait_for_timeout(800)
                print(f"  │ ✅ DOM button[{dom_index}] bosildi: '{text[:40]}'")
                return True
        except Exception as ex:
            print(f"  │ ❌ DOM click xato: {ex}")
        return False
