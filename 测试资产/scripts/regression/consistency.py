# -*- coding: utf-8 -*-
import sys, sqlite3, time, statistics
sys.path.insert(0, r"C:\Users\Lenovo\Desktop\lit_agent\作品相关代码\scr")
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.retrieve.trace import QuoteTracer
s = get_settings(); store = Store(s.db_path); store.init_schema()
tr = QuoteTracer(store, s)
con = sqlite3.connect(str(s.db_path))
raw = con.execute("""select t.text from chunks c join chunk_text t on t.chunk_id=c.id
   join documents d on d.id=c.doc_id where d.has_fulltext=1 and length(t.text)>1200 limit 1""").fetchone()[0]

def run(q):
    ts=[]
    for _ in range(3):
        t0=time.perf_counter(); r,_=tr.trace(q, allow_online=False); ts.append((time.perf_counter()-t0)*1000)
    return r.level, round(r.confidence,4), round(statistics.median(ts),1)

# 同一段文字，两种空白形态
seg = raw[100:220]
seg_norm = " ".join(seg.split())
print("原片段含换行:", "\n" in seg)
print("%-26s %-14s %-8s %s" % ("形态", "等级", "置信度", "耗时ms"))
for name, q in [("原文（含换行）", seg), ("空白归一化", seg_norm)]:
    lv, cf, ms = run(q)
    print("%-26s %-14s %-8.4f %.1f" % (name, lv, cf, ms))
print("两种形态结论一致:", run(seg)[:2] == run(seg_norm)[:2])
