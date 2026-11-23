# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys, sqlite3, time, statistics
sys.path.insert(0, str(_REPO_ROOT / '作品相关代码' / 'scr'))
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.retrieve.trace import QuoteTracer
s = get_settings(); store = Store(s.db_path); store.init_schema()
tracer = QuoteTracer(store, s)
con = sqlite3.connect(str(s.db_path))
raw = " ".join(con.execute("""select t.text from chunks c join chunk_text t on t.chunk_id=c.id
   join documents d on d.id=c.doc_id where d.has_fulltext=1 and length(t.text)>1200 limit 1""").fetchone()[0].split())
# 预热
for _ in range(5): tracer.trace(raw[200:320], allow_online=False)
print("%-10s %-10s %-9s %-9s %s" % ("输入字数", "中位ms", "最小ms", "最大ms", "词元"))
for L in (30, 50, 80, 120, 200, 400, 800, 2000, 6000):
    q = (raw * 20)[:L]
    ts = []
    for _ in range(8):
        t0 = time.perf_counter(); r, rp = tracer.trace(q, allow_online=False); ts.append((time.perf_counter()-t0)*1000)
    ts.sort()
    print("%-10d %-10.2f %-9.2f %-9.2f %d" % (L, statistics.median(ts), ts[0], ts[-1], rp.quote_tokens))
