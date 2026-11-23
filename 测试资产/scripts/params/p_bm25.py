# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys, sqlite3, copy, time
sys.path.insert(0, str(_REPO_ROOT / '作品相关代码' / 'scr'))
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.index.lexical import BM25Index
from litagent.retrieve.search import HybridSearcher
base = get_settings(); store = Store(base.db_path); store.init_schema()
con = sqlite3.connect(str(base.db_path))
q = " ".join(con.execute("select text from chunk_text where length(text)>1000 limit 1").fetchone()[0].split())[:110]

print("=== BM25 k1/b 扫描（前 3 名）===")
def top(k1,b):
    ix = BM25Index(store, k1=k1, b=b, tokenizer_cache_dir=base.cache_dir)
    return [cid.split("#")[-1][:8] for cid,_ in ix.search(q, top_k=3)]
base_top = None
for k1 in (0.9, 1.5, 2.0):
    for b in (0.3, 0.75, 0.9):
        t = top(k1,b)
        if base_top is None: base_top = t
        print("  k1=%-4.1f b=%-5.2f -> %-28s %s" % (k1, b, str(t), "" if t==base_top else "排序变化"))
print("\n=== rrf_k 扫描 ===")
for k in (10, 30, 60, 100):
    s2 = copy.deepcopy(base); s2.rrf_k = k
    r = HybridSearcher(store, s2).search(q, top_k=5)
    hits = r[0] if isinstance(r, tuple) else r
    print("  rrf_k=%-4d -> %s" % (k, [h.chunk_id.split("#")[-1][:8] for h in hits[:3]]))
print("\n=== top_k 扫描 ===")
for tk in (3, 5, 10, 20):
    r = HybridSearcher(store, base).search(q, top_k=tk)
    hits = r[0] if isinstance(r, tuple) else r
    print("  top_k=%-3d -> 返回 %d 条" % (tk, len(hits)))
