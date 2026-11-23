# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys, sqlite3, time
sys.path.insert(0, str(_REPO_ROOT / '作品相关代码' / 'scr'))
t0=time.time()
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.clean.chunker import chunk_text
print("import %.2fs" % (time.time()-t0), flush=True)
base = get_settings(); store = Store(base.db_path); store.init_schema()
con = sqlite3.connect(str(base.db_path))
raw = con.execute("select text from chunk_text where length(text)>1500 limit 1").fetchone()[0]
long_text = (raw + "\n\n") * 3
print("文本 %d 字" % len(long_text), flush=True)
for m in (400, 800, 1800):
    t1=time.time()
    cs = chunk_text("t", long_text, max_chars=m, overlap_chars=100, min_chars=20)
    mx = max((len(c.text) for c in cs), default=0)
    print("max=%-5d 片段=%-4d 最长=%-6d %.2fs" % (m, len(cs), mx, time.time()-t1), flush=True)
