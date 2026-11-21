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

def bench(q, n=5):
    ts=[]
    for _ in range(n):
        t0=time.perf_counter(); tr.trace(q, allow_online=False); ts.append((time.perf_counter()-t0)*1000)
    return statistics.median(ts)

# 取一段含换行的原文片段
seg = raw[100:160]
norm = " ".join(seg.split())
print("原片段含换行:", "\n" in seg, "| 长度:", len(seg))
print()
print("%-28s %-10s %s" % ("查询形式", "中位ms", "说明"))
print("%-28s %-10.2f %s" % ("原文片段（保留换行）", bench(seg), "可直接 SQL 定位"))
print("%-28s %-10.2f %s" % ("同一片段（空白归一化）", bench(norm), "SQL 定位失败则回退慢路径" if norm != seg else "与上相同"))

# 无换行的整段（确认为原文子串）
plain = None
import re
for m in re.finditer(r"[^\n]{60,}", raw):
    plain = m.group()[:60]; break
if plain:
    print("%-28s %-10.2f %s" % ("无换行原文 60 字", bench(plain), "可直接定位"))

# 加前缀干扰（非原样）
if plain:
    print("%-28s %-10.2f %s" % ("前缀干扰 + 原文", bench("extra unrelated words " + plain), "非原样子串"))
