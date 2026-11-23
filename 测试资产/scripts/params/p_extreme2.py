# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import subprocess, sys, textwrap
PY = sys.executable
TPL = textwrap.dedent('''
    import sys, sqlite3
    sys.path.insert(0, str(_REPO_ROOT / '作品相关代码' / 'scr'))
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
    ("max=0（已确认）", 0, 0, 20),
    ("max=1 min=1", 1, 0, 1),
    ("max=200 overlap=500", 200, 500, 20),
    ("max=200 min_chars=500", 200, 50, 500),
    ("max=800 overlap=400", 800, 400, 20),
    ("max=800 overlap=0", 800, 0, 20),
]
for name, mc, oc, mn in cases:
    code = TPL % (mc, oc, mn)
    try:
        r = subprocess.run([PY, "-c", code], capture_output=True, text=True,
                           timeout=10, encoding="utf-8", errors="replace")
        out = (r.stdout or "").strip()
        if not out:
            out = ((r.stderr or "").strip().splitlines() or ["<无输出>"])[-1][:80]
        print("%-24s -> %s" % (name, out[:95]))
    except subprocess.TimeoutExpired:
        print("%-24s -> 超时未返回" % name)
