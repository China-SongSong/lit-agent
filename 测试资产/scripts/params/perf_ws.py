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

def bench(q, n=5):
    ts=[]
    for _ in range(n):
        t0=time.perf_counter(); r,_=tr.trace(q, allow_online=False); ts.append((time.perf_counter()-t0)*1000)
    return statistics.median(ts), r.level

print("%-34s %-10s %-14s %s" % ("形态", "中位ms", "等级", "说明"))
cases = []
for L in (50, 80, 120):
    seg = raw[:L]
    cases.append(("原文前%d字（含换行）" % L, seg, "原样"))
    cases.append(("同段空白归一化 %d" % L, " ".join(seg.split()), "归一化"))
    cases.append(("同段多空格 %d" % L, "  ".join(seg.split()), "重复空格"))
    cases.append(("同段制表符 %d" % L, "\t".join(seg.split()).replace("\n"," "), "制表符"))
slow = []
for name, q, tag in cases:
    ms, lv = bench(q)
    print("%-34s %-10.2f %-14s %s" % (name, ms, lv, tag))
    if tag != "原样" and ms > 60: slow.append((name, ms))

print()
if slow:
    print("仍有慢路径形态 %d 个:" % len(slow))
    for n, m in slow[:5]: print("   ", n, "%.1f ms" % m)
else:
    print("所有空白变体均已走快路径（<60ms）")
