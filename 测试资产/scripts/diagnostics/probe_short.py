# -*- coding: utf-8 -*-
import sys, sqlite3, time
sys.path.insert(0, r"C:\Users\Lenovo\Desktop\lit_agent\作品相关代码\scr")
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.index.phrase import exact_tokens
s = get_settings(); store = Store(s.db_path); store.init_schema()
con = sqlite3.connect(str(s.db_path))
raw = " ".join(con.execute("""select t.text from chunks c join chunk_text t on t.chunk_id=c.id
   join documents d on d.id=c.doc_id where d.has_fulltext=1 and length(t.text)>1200 limit 1""").fetchone()[0].split())
print("%-8s %-26s %-8s %s" % ("字数", "词元", "候选片段", "说明"))
for L in (30, 50, 80, 120, 800):
    q = (raw * 20)[:L]
    toks = exact_tokens(q)
    postings = store.postings_for_terms(toks, max_df_ratio=1.0)
    cand = {cid for chain in postings.values() for cid, _ in chain}
    print("%-8d %-26s %-8d %s" % (L, " ".join(toks[:6])[:26], len(cand),
          "候选取并集，常见词导致全库扫描" if len(cand) > 150 else ""))

# 单独测：全量分词扫描 254 段的代价
t0 = time.perf_counter()
for row in con.execute("select t.text from chunk_text t"):
    exact_tokens(row[0])
dt = (time.perf_counter()-t0)*1000
print("\n全库 %d 段做一次分词扫描: %.1f ms" % (con.execute("select count(*) from chunk_text").fetchone()[0], dt))
