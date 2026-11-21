# -*- coding: utf-8 -*-
import sys, sqlite3, time, statistics
sys.path.insert(0, r"C:\Users\Lenovo\Desktop\lit_agent\作品相关代码\scr")
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.index.phrase import exact_tokens_with_spans, normalized_exact_text
s = get_settings(); store = Store(s.db_path); store.init_schema()
con = sqlite3.connect(str(s.db_path))
raw = con.execute("select text from chunk_text where length(text)>1200 limit 1").fetchone()[0]
nq = normalized_exact_text(raw[:50])
rows = store.direct_normalized_matches(nq)
print("50 字查询的规范化命中数:", len(rows))
texts = [store.get_chunk_text(cid) or "" for cid,_ in rows][:6]
ts=[]
for t in texts:
    t0=time.perf_counter(); exact_tokens_with_spans(t); ts.append((time.perf_counter()-t0)*1000)
print("单片段分词定位耗时: 中位 %.2f ms（n=%d，片段长约 %d 字）" % (statistics.median(ts), len(ts), len(texts[0]) if texts else 0))
print("按 %d 个候选估算: %.0f ms" % (len(texts), statistics.median(ts)*len(texts)))
