# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys, sqlite3, time, statistics
sys.path.insert(0, str(_REPO_ROOT / '作品相关代码' / 'scr'))
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.retrieve.trace import QuoteTracer
from litagent.index.phrase import normalized_exact_text, exact_tokens_with_spans
s = get_settings(); store = Store(s.db_path); store.init_schema()
tr = QuoteTracer(store, s)
con = sqlite3.connect(str(s.db_path))
raw = con.execute("""select t.text from chunks c join chunk_text t on t.chunk_id=c.id
   join documents d on d.id=c.doc_id where d.has_fulltext=1 and length(t.text)>1200 limit 1""").fetchone()[0]
seg = raw[:50]
nq = normalized_exact_text(seg)
print("查询:", repr(seg))
print("归一化:", repr(nq))
rows = store.direct_normalized_matches(nq)
print("规范化直接命中数:", len(rows))
for cid, txt in rows[:5]:
    pos = (txt or "").find("")
    print("   片段 %s 长度 %d" % (cid[:40], len(txt or "")))
# 分词定位代价
ts=[]
for cid, txt in rows[:5]:
    t0=time.perf_counter(); exact_tokens_with_spans(txt or ""); ts.append((time.perf_counter()-t0)*1000)
if ts: print("逐片段分词定位: 中位 %.2f ms × %d = %.0f ms" % (statistics.median(ts), len(ts), statistics.median(ts)*len(ts)))
t0=time.perf_counter(); hits=tr.phrase.search(seg, top_k=5, min_ratio=0.25, with_text=True); d1=(time.perf_counter()-t0)*1000
print("phrase.search: %.1f ms，命中 %d" % (d1, len(hits)))
t0=time.perf_counter(); sp=tr._best_span(seg); d2=(time.perf_counter()-t0)*1000
print("_best_span: %.1f ms" % d2)
