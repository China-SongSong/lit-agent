# -*- coding: utf-8 -*-
import sys, sqlite3, time
sys.path.insert(0, r"C:\Users\Lenovo\Desktop\lit_agent\作品相关代码\scr")
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.index.phrase import normalized_exact_text, exact_tokens
s = get_settings(); store = Store(s.db_path); store.init_schema()
con = sqlite3.connect(str(s.db_path))
raw = con.execute("""select t.text from chunks c join chunk_text t on t.chunk_id=c.id
   join documents d on d.id=c.doc_id where d.has_fulltext=1 and length(t.text)>1200 limit 1""").fetchone()[0]

print("=== 表结构 ===")
cols = [r[1] for r in con.execute("PRAGMA table_info(chunk_text)").fetchall()]
print("chunk_text 列:", cols)

for L in (50, 80, 120):
    seg = raw[:L]
    toks = exact_tokens(seg)
    nq = normalized_exact_text(seg)
    t0 = time.perf_counter()
    rows = store.direct_normalized_matches(nq)
    dt = (time.perf_counter()-t0)*1000
    print("\n长度 %d: 词元=%d 归一化长度=%d 直接命中=%d 条 耗时=%.2f ms" % (L, len(toks), len(nq), len(rows), dt))
    print("  归一化样例: %r" % nq[:60])
