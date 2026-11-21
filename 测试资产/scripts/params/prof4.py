# -*- coding: utf-8 -*-
import sys, sqlite3, time, statistics
sys.path.insert(0, r"C:\Users\Lenovo\Desktop\lit_agent\作品相关代码\scr")
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.retrieve.trace import QuoteTracer
from litagent.index.phrase import exact_tokens
s = get_settings(); store = Store(s.db_path); store.init_schema()
tr = QuoteTracer(store, s)
con = sqlite3.connect(str(s.db_path))
raw = con.execute("""select t.text from chunks c join chunk_text t on t.chunk_id=c.id
   join documents d on d.id=c.doc_id where d.has_fulltext=1 and length(t.text)>1200 limit 1""").fetchone()[0]

def bench(q, n=3):
    ts=[]
    for _ in range(n):
        t0=time.perf_counter(); r,_=tr.trace(q, allow_online=False); ts.append((time.perf_counter()-t0)*1000)
    return statistics.median(ts), r.level

cases = {
  "硬截 50 字（切在词中）": raw[:50],
  "词边界截断（约 50 字）": raw[:50].rsplit(" ",1)[0],
  "硬截 80 字": raw[:80],
  "词边界截断（约 80 字）": raw[:80].rsplit(" ",1)[0],
}
print("%-26s %-10s %-6s %s" % ("切法", "中位ms", "词元", "等级"))
for name, q in cases.items():
    ms, lv = bench(q)
    print("%-26s %-10.1f %-6d %s" % (name, ms, len(exact_tokens(q)), lv))
