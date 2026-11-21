# -*- coding: utf-8 -*-
import subprocess, sys, textwrap, os
PY = sys.executable
TPL = textwrap.dedent('''
    import sys, sqlite3
    sys.path.insert(0, r"C:\\Users\\Lenovo\\Desktop\\lit_agent\\作品相关代码\\scr")
    from litagent.config import get_settings
    from litagent.index.store import Store
    from litagent.clean.chunker import chunk_text
    base = get_settings(); Store(base.db_path).init_schema()
    con = sqlite3.connect(str(base.db_path))
    raw = con.execute("select text from chunk_text where length(text)>1500 limit 1").fetchone()[0]
    t = (raw + "\\n\\n") * 3
    cs = chunk_text("t", t, max_chars=%d, overlap_chars=%d, min_chars=%d)
    print("OK 片段数=%%d 最长=%%d" %% (len(cs), max((len(c.text) for c in cs), default=0)))
''')
cases = [
    ("max=0 overlap=0 min=20", 0, 0, 20),
    ("max=1 overlap=0 min=1", 1, 0, 1),
    ("max=200 overlap=500", 200, 500, 20),
    ("max=200 min_chars=500", 200, 50, 500),
    ("max=800 overlap=400", 800, 400, 20),
    ("max=800 overlap=0", 800, 0, 20),
]
for name, mc, oc, mn in cases:
    code = TPL % (mc, oc, mn)
    try:
        r = subprocess.run([PY, "-c", code], capture_output=True, text=True, timeout=12)
        out = (r.stdout or "").strip() or (r.stderr or "").strip().splitlines()[-1][:80]
        print("%-28s -> %s" % (name, out[:90]))
    except subprocess.TimeoutExpired:
        print("%-28s -> 超时未返回（疑似死循环）" % name)
