"""
DB Viewer — bazadagi barcha ma'lumotlarni ko'rish uchun.
Ishlatish: python show_db.py [table_name]
Jadvallar: credentials, page_elements, user_hints, navigation_paths,
           form_knowledge, test_runs, step_results
"""
import sys
import json
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "memory", "qa_memory.db")

TABLES = [
    "credentials",
    "page_elements",
    "user_hints",
    "navigation_paths",
    "form_knowledge",
    "test_runs",
    "step_results",
]

def sep(char="═", n=70):
    print(char * n)

def show_credentials(conn):
    sep()
    print("  🔑 CREDENTIALS (login ma'lumotlari)")
    sep()
    rows = conn.execute("SELECT id, site_url, email, created_at FROM credentials").fetchall()
    if not rows:
        print("  (bo'sh)")
        return
    for r in rows:
        print(f"  [{r['id']}] {r['site_url']}")
        print(f"       email: {r['email']}")
        print(f"       vaqt : {r['created_at']}")

def show_page_elements(conn):
    sep()
    print("  📌 PAGE ELEMENTS (sahifa elementlari)")
    sep()
    rows = conn.execute(
        "SELECT id, site_url, page_url, element_name, element_type, css_selector, xpath, visible_text FROM page_elements ORDER BY site_url, page_url"
    ).fetchall()
    if not rows:
        print("  (bo'sh)")
        return
    cur_page = None
    for r in rows:
        if r['page_url'] != cur_page:
            cur_page = r['page_url']
            print(f"\n  📄 {cur_page}")
        print(f"    [{r['id']}] {r['element_name']} ({r['element_type']})")
        print(f"         css  : {r['css_selector']}")
        print(f"         xpath: {r['xpath']}")
        if r['visible_text']:
            print(f"         text : {r['visible_text']}")

def show_user_hints(conn):
    sep()
    print("  💡 USER HINTS (foydalanuvchi ko'rsatmalari)")
    sep()
    rows = conn.execute(
        "SELECT id, site_url, action_keyword, hint, nav_path, created_at FROM user_hints ORDER BY site_url"
    ).fetchall()
    if not rows:
        print("  (bo'sh)")
        return
    for r in rows:
        print(f"  [{r['id']}] kalit: '{r['action_keyword']}'")
        print(f"       sayt : {r['site_url']}")
        print(f"       hint : {r['hint']}")
        if r['nav_path'] and r['nav_path'] != '[]':
            print(f"       yo'l : {r['nav_path']}")
        print(f"       vaqt : {r['created_at']}")
        print()

def show_navigation_paths(conn):
    sep()
    print("  🗺️  NAVIGATION PATHS (navigatsiya yo'llari)")
    sep()
    rows = conn.execute(
        "SELECT id, site_url, action_name, steps, final_url, created_at FROM navigation_paths ORDER BY site_url"
    ).fetchall()
    if not rows:
        print("  (bo'sh)")
        return
    for r in rows:
        print(f"  [{r['id']}] {r['action_name']}")
        print(f"       sayt      : {r['site_url']}")
        print(f"       final_url : {r['final_url']}")
        try:
            steps = json.loads(r['steps'])
            print(f"       qadamlar  : {len(steps)} ta")
            for i, s in enumerate(steps, 1):
                stype = s.get('type', '')
                if stype == 'navigate':
                    print(f"         {i}. [navigate] → {s.get('url','')}")
                elif stype == 'login':
                    print(f"         {i}. [login] success={s.get('success')}")
                elif stype == 'click':
                    print(f"         {i}. [click] '{s.get('element','')}' css='{s.get('css','')}' → {s.get('resulted_url','')}")
                elif stype == 'fill':
                    print(f"         {i}. [fill] '{s.get('field','')}' css='{s.get('css','')}'")
                else:
                    print(f"         {i}. {s}")
        except Exception:
            print(f"       steps: {r['steps'][:100]}")
        print(f"       vaqt : {r['created_at']}")
        print()

def show_form_knowledge(conn):
    sep()
    print("  📋 FORM KNOWLEDGE (formalar)")
    sep()
    rows = conn.execute(
        "SELECT id, site_url, form_name, form_url, fields, submit_selector FROM form_knowledge ORDER BY site_url"
    ).fetchall()
    if not rows:
        print("  (bo'sh)")
        return
    for r in rows:
        print(f"  [{r['id']}] {r['form_name']} @ {r['form_url']}")
        print(f"       sayt  : {r['site_url']}")
        print(f"       submit: {r['submit_selector']}")
        try:
            fields = json.loads(r['fields'])
            print(f"       maydonlar ({len(fields)} ta):")
            for f in fields:
                print(f"         - {f.get('label','?')} ({f.get('type','?')}) css='{f.get('css_selector','')}'")
        except Exception:
            print(f"       fields: {r['fields'][:100]}")
        print()

def show_test_runs(conn):
    sep()
    print("  🧪 TEST RUNS (test natijalari)")
    sep()
    rows = conn.execute(
        "SELECT id, test_name, site_url, status, created_at FROM test_runs ORDER BY created_at DESC LIMIT 20"
    ).fetchall()
    if not rows:
        print("  (bo'sh)")
        return
    for r in rows:
        icon = "✅" if r['status'] == 'passed' else ("⏳" if r['status'] == 'running' else "❌")
        print(f"  {icon} [{r['id']}] {r['test_name']}")
        print(f"       sayt  : {r['site_url']}")
        print(f"       holat : {r['status']}")
        print(f"       vaqt  : {r['created_at']}")
        print()

def show_step_results(conn, test_run_id=None):
    sep()
    print(f"  📊 STEP RESULTS{f' (test #{test_run_id})' if test_run_id else ' (oxirgi 30 ta)'}")
    sep()
    if test_run_id:
        rows = conn.execute(
            "SELECT * FROM step_results WHERE test_run_id=? ORDER BY step_id",
            (test_run_id,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM step_results ORDER BY created_at DESC LIMIT 30"
        ).fetchall()
    if not rows:
        print("  (bo'sh)")
        return
    for r in rows:
        icon = "✅" if r['status'] == 'passed' else "❌"
        print(f"  {icon} test#{r['test_run_id']} qadam#{r['step_id']} [{r['action_type']}] {r['status'].upper()}")
        print(f"       tavsif: {r['description']}")
        if r['error_message']:
            print(f"       xato  : {r['error_message']}")
        print()

def show_summary(conn):
    sep()
    print("  📦 DB XULOSA")
    sep()
    for t in TABLES:
        try:
            c = conn.execute(f"SELECT COUNT(*) as n FROM {t}").fetchone()['n']
            print(f"  {t:<20}: {c} ta yozuv")
        except Exception as e:
            print(f"  {t:<20}: xato — {e}")
    sep()

def main():
    if not os.path.exists(DB_PATH):
        print(f"❌ DB topilmadi: {DB_PATH}")
        print("   Avval main.py ni ishga tushiring.")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    arg = sys.argv[1].lower() if len(sys.argv) > 1 else "all"

    if arg == "all" or arg == "summary":
        show_summary(conn)

    if arg in ("all", "credentials", "creds", "c"):
        show_credentials(conn)

    if arg in ("all", "elements", "page_elements", "el"):
        show_page_elements(conn)

    if arg in ("all", "hints", "user_hints", "h"):
        show_user_hints(conn)

    if arg in ("all", "nav", "navigation_paths", "n"):
        show_navigation_paths(conn)

    if arg in ("all", "forms", "form_knowledge", "f"):
        show_form_knowledge(conn)

    if arg in ("all", "tests", "test_runs", "t"):
        show_test_runs(conn)

    if arg in ("all", "steps", "step_results", "s"):
        # test_run_id berilgan bo'lsa
        run_id = None
        if len(sys.argv) > 2:
            try:
                run_id = int(sys.argv[2])
            except ValueError:
                pass
        show_step_results(conn, run_id)

    if arg not in ("all", "summary", "credentials", "creds", "c",
                   "elements", "page_elements", "el",
                   "hints", "user_hints", "h",
                   "nav", "navigation_paths", "n",
                   "forms", "form_knowledge", "f",
                   "tests", "test_runs", "t",
                   "steps", "step_results", "s"):
        print(f"Noma'lum jadval: '{arg}'")
        print("Mavjud: all, creds, elements, hints, nav, forms, tests, steps")

    conn.close()

    print("\nIshlitish:")
    print("  python show_db.py              # barchasi")
    print("  python show_db.py creds        # loginlar")
    print("  python show_db.py hints        # user hint'lar")
    print("  python show_db.py elements     # sahifa elementlari")
    print("  python show_db.py nav          # navigatsiya yo'llari")
    print("  python show_db.py forms        # formalar")
    print("  python show_db.py tests        # test natijalari")
    print("  python show_db.py steps        # qadam natijalari")
    print("  python show_db.py steps 3      # test #3 ning qadamlari")

if __name__ == "__main__":
    main()
