# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys, sqlite3, time, statistics
sys.path.insert(0, str(_REPO_ROOT / '作品相关代码' / 'scr'))
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.retrieve.trace import QuoteTracer
s = get_settings(); store = Store(s.db_path); store.init_schema()
tr = QuoteTracer(store, s)
con = sqlite3.connect(str(s.db_path))
raw = con.execute("""select t.text from chunks c join chunk_text t on t.chunk_id=c.id
   join documents d on d.id=c.doc_id where d.has_fulltext=1 and length(t.text)>1200 limit 1""").fetchone()[0]

for L in (50, 80):
    seg = raw[:L]
    print("\n=== 长度 %d ===" % L)
    # 总耗时
    ts=[]
    for _ in range(3):
        t0=time.perf_counter(); tr.trace(seg, allow_online=False); ts.append((time.perf_counter()-t0)*1000)
    print("  trace 总耗时: %.2f ms" % statistics.median(ts))
    # phrase.search
    ts=[]
    for _ in range(3):
        t0=time.perf_counter(); hits=tr.phrase.search(seg, top_k=5, min_ratio=0.25, with_text=True); ts.append((time.perf_counter()-t0)*1000)
    print("  phrase.search: %.2f ms (命中 %d)" % (statistics.median(ts), len(hits)))
    # _best_span
    ts=[]
    for _ in range(3):
        t0=time.perf_counter(); sp=tr._best_span(seg); ts.append((time.perf_counter()-t0)*1000)
    print("  _best_span:    %.2f ms (%s)" % (statistics.median(ts), "有结果" if sp else "None"))
    # bm25 旁证
    if hits:
        ts=[]
        for _ in range(3):
            t0=time.perf_counter(); tr._check_bm25_support(seg, hits[0].chunk_id); ts.append((time.perf_counter()-t0)*1000)
        print("  bm25 旁证:     %.2f ms" % statistics.median(ts))
