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
    cs = chunk_text("t", (raw+"\\n\\n")*3, max_chars=%d, overlap_chars=0, min_chars=1)
    print("OK 片段数=%%d" %% len(cs))
''')
for name, mc in [("max_chars=-1", -1), ("max_chars=2", 2), ("max_chars=10", 10)]:
    try:
        r = subprocess.run([PY, "-c", TPL % mc], capture_output=True, text=True,
                           timeout=10, encoding="utf-8", errors="replace")
        out = (r.stdout or "").strip() or ((r.stderr or "").strip().splitlines() or ["<无>"])[-1][:70]
        print("%-16s -> %s" % (name, out[:80]))
    except subprocess.TimeoutExpired:
        print("%-16s -> 超时（死循环）" % name)

# agent_max_rounds 负数是否被夹住
sys.path.insert(0, str(_REPO_ROOT / '作品相关代码' / 'scr'))
import inspect, litagent.agent.loop as L
src = inspect.getsource(L.LiteratureAgent.run)
for line in src.splitlines():
    if "limit=" in line or "max_rounds" in line:
        print("loop.py:", line.strip()[:110])
