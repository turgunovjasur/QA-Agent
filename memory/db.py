import sqlite3
import json
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "qa_memory.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    cur = conn.cursor()
    cur.executescript("""
        -- Login ma'lumotlari
        CREATE TABLE IF NOT EXISTS credentials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_url TEXT NOT NULL UNIQUE,
            email TEXT NOT NULL,
            password TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Sahifadagi elementlar va ularning locatorlari
        CREATE TABLE IF NOT EXISTS page_elements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_url TEXT NOT NULL,
            page_url TEXT NOT NULL,
            element_name TEXT NOT NULL,
            element_type TEXT,
            css_selector TEXT,
            xpath TEXT,
            visible_text TEXT,
            label_text TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(page_url, element_name)
        );

        -- Navigatsiya yo'llari (qaysi sahifaga qanday borish)
        CREATE TABLE IF NOT EXISTS navigation_paths (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_url TEXT NOT NULL,
            action_name TEXT NOT NULL,
            steps TEXT NOT NULL,
            final_url TEXT,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(site_url, action_name)
        );

        -- Forma ma'lumotlari (qaysi formada qanday maydonlar bor)
        CREATE TABLE IF NOT EXISTS form_knowledge (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_url TEXT NOT NULL,
            form_name TEXT NOT NULL,
            form_url TEXT,
            fields TEXT NOT NULL,
            submit_selector TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(site_url, form_name)
        );

        -- Test checklist va natijalari
        CREATE TABLE IF NOT EXISTS test_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_name TEXT NOT NULL,
            site_url TEXT NOT NULL,
            prompt TEXT NOT NULL,
            status TEXT NOT NULL,
            steps TEXT NOT NULL,
            token_summary TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- User tomonidan berilgan yo'nalish ma'lumotlari
        CREATE TABLE IF NOT EXISTS user_hints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_url TEXT NOT NULL,
            action_keyword TEXT NOT NULL,
            hint TEXT NOT NULL,
            nav_path TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(site_url, action_keyword)
        );

        -- Har bir qadam natijasi
        CREATE TABLE IF NOT EXISTS step_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_run_id INTEGER NOT NULL,
            step_id INTEGER NOT NULL,
            description TEXT,
            action_type TEXT,
            status TEXT,
            token_info TEXT,
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (test_run_id) REFERENCES test_runs(id)
        );
    """)
    conn.commit()
    conn.close()


# ─── CREDENTIALS ──────────────────────────────────────────────

def save_credentials(site_url: str, email: str, password: str):
    conn = get_connection()
    conn.execute("""
        INSERT INTO credentials (site_url, email, password)
        VALUES (?, ?, ?)
        ON CONFLICT(site_url) DO UPDATE SET email=excluded.email, password=excluded.password
    """, (site_url, email, password))
    conn.commit()
    conn.close()


def get_credentials(site_url: str) -> dict:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM credentials WHERE site_url = ?", (site_url,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ─── PAGE ELEMENTS ────────────────────────────────────────────

def save_page_element(site_url: str, page_url: str, element_name: str,
                      element_type: str = "", css_selector: str = "",
                      xpath: str = "", visible_text: str = "", label_text: str = ""):
    conn = get_connection()
    conn.execute("""
        INSERT INTO page_elements
            (site_url, page_url, element_name, element_type, css_selector, xpath, visible_text, label_text)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(page_url, element_name) DO UPDATE SET
            element_type=excluded.element_type,
            css_selector=excluded.css_selector,
            xpath=excluded.xpath,
            visible_text=excluded.visible_text,
            label_text=excluded.label_text
    """, (site_url, page_url, element_name, element_type, css_selector, xpath, visible_text, label_text))
    conn.commit()
    conn.close()


def get_page_elements(page_url: str) -> list:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM page_elements WHERE page_url = ?", (page_url,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── NAVIGATION PATHS ─────────────────────────────────────────

def save_navigation_path(site_url: str, action_name: str, steps: list,
                         final_url: str = "", description: str = ""):
    conn = get_connection()
    conn.execute("""
        INSERT INTO navigation_paths (site_url, action_name, steps, final_url, description)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(site_url, action_name) DO UPDATE SET
            steps=excluded.steps, final_url=excluded.final_url, description=excluded.description
    """, (site_url, action_name, json.dumps(steps, ensure_ascii=False), final_url, description))
    conn.commit()
    conn.close()


def get_navigation_path(site_url: str, action_name: str) -> dict:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM navigation_paths WHERE site_url=? AND action_name=?",
        (site_url, action_name)
    ).fetchone()
    conn.close()
    if row:
        data = dict(row)
        data["steps"] = json.loads(data["steps"])
        return data
    return None


# ─── FORM KNOWLEDGE ───────────────────────────────────────────

def save_form_knowledge(site_url: str, form_name: str, form_url: str,
                        fields: list, submit_selector: str = ""):
    conn = get_connection()
    conn.execute("""
        INSERT INTO form_knowledge (site_url, form_name, form_url, fields, submit_selector)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(site_url, form_name) DO UPDATE SET
            form_url=excluded.form_url, fields=excluded.fields, submit_selector=excluded.submit_selector
    """, (site_url, form_name, form_url, json.dumps(fields, ensure_ascii=False), submit_selector))
    conn.commit()
    conn.close()


def get_form_knowledge(site_url: str, form_name: str) -> dict:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM form_knowledge WHERE site_url=? AND form_name=?",
        (site_url, form_name)
    ).fetchone()
    conn.close()
    if row:
        data = dict(row)
        data["fields"] = json.loads(data["fields"])
        return data
    return None


# ─── TEST RUNS ────────────────────────────────────────────────

def save_test_run(test_name: str, site_url: str, prompt: str, status: str,
                  steps: list, token_summary: dict) -> int:
    conn = get_connection()
    cur = conn.execute("""
        INSERT INTO test_runs (test_name, site_url, prompt, status, steps, token_summary)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (test_name, site_url, prompt, status,
          json.dumps(steps, ensure_ascii=False),
          json.dumps(token_summary, ensure_ascii=False)))
    run_id = cur.lastrowid
    conn.commit()
    conn.close()
    return run_id


def save_step_result(test_run_id: int, step_id: int, description: str,
                     action_type: str, status: str, token_info: dict, error_message: str = ""):
    conn = get_connection()
    conn.execute("""
        INSERT INTO step_results
            (test_run_id, step_id, description, action_type, status, token_info, error_message)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (test_run_id, step_id, description, action_type, status,
          json.dumps(token_info, ensure_ascii=False), error_message))
    conn.commit()
    conn.close()


# ─── USER HINTS ───────────────────────────────────────────────

def save_user_hint(site_url: str, action_keyword: str, hint: str, nav_path: list = None):
    """User bergan yo'nalish ko'rsatmasini saqlaydi."""
    conn = get_connection()
    conn.execute("""
        INSERT INTO user_hints (site_url, action_keyword, hint, nav_path)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(site_url, action_keyword) DO UPDATE SET
            hint=excluded.hint, nav_path=excluded.nav_path
    """, (site_url, action_keyword, hint,
          json.dumps(nav_path or [], ensure_ascii=False)))
    conn.commit()
    conn.close()


def get_user_hint(site_url: str, action_keyword: str):
    """Saqlangan user hint'ini oladi."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM user_hints WHERE site_url=? AND action_keyword=?",
        (site_url, action_keyword)
    ).fetchone()
    conn.close()
    if row:
        data = dict(row)
        data["nav_path"] = json.loads(data.get("nav_path") or "[]")
        return data
    return None


def search_user_hints(site_url: str, keywords: list):
    """
    Kalit so'zlar bo'yicha user hint'larini qidiradi.
    Masalan: ['product', 'mahsulot'] → 'product navbarda spravichnik ichida'
    """
    conn = get_connection()
    results = []
    for kw in keywords:
        rows = conn.execute(
            "SELECT * FROM user_hints WHERE site_url=? AND action_keyword LIKE ?",
            (site_url, f"%{kw.lower()}%")
        ).fetchall()
        for r in rows:
            data = dict(r)
            data["nav_path"] = json.loads(data.get("nav_path") or "[]")
            results.append(data)
    conn.close()
    return results


def get_all_test_runs() -> list:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM test_runs ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def show_db_summary():
    """Terminal uchun DB statistikasi."""
    conn = get_connection()
    print("\n" + "═" * 50)
    print("  📦 DB XOTIRA HOLATI")
    print("═" * 50)

    creds = conn.execute("SELECT COUNT(*) as c FROM credentials").fetchone()["c"]
    elements = conn.execute("SELECT COUNT(*) as c FROM page_elements").fetchone()["c"]
    nav = conn.execute("SELECT COUNT(*) as c FROM navigation_paths").fetchone()["c"]
    forms = conn.execute("SELECT COUNT(*) as c FROM form_knowledge").fetchone()["c"]
    tests = conn.execute("SELECT COUNT(*) as c FROM test_runs").fetchone()["c"]

    print(f"  Credentials  : {creds} ta sayt")
    print(f"  Page elements: {elements} ta element")
    print(f"  Nav paths    : {nav} ta yo'l")
    print(f"  Forms        : {forms} ta forma")
    print(f"  Test runs    : {tests} ta test")
    print("═" * 50)
    conn.close()
