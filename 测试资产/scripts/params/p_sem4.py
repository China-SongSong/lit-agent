# -*- coding: utf-8 -*-
import sys, sqlite3, copy, traceback
sys.path.insert(0, r"C:\Users\Lenovo\Desktop\lit_agent\作品相关代码\scr")
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.clean.chunker import chunk_text
from litagent.index.lexical import BM25Index
from litagent.retrieve.search import HybridSearcher
base = get_settings(); store = Store(base.db_path); store.init_schema()
con = sqlite3.connect(str(base.db_path))
raw = con.execute("select text from chunk_text where length(text)>1500 limit 1").fetchone()[0]
long_text = (raw + "\n\n") * 3
print("测试文本长度: %d 字" % len(long_text))

print("\n=== max_chunk_chars 扫描 ===")
print("%-8s %-8s %-10s %s" % ("上限","片段数","最长片段","是否超限"))
for m in (400, 800, 1200, 1800, 3000):
    cs = chunk_text("t", long_text, max_chars=m, overlap_chars=100, min_chars=20)
    mx = max((len(c.text) for c in cs), default=0)
    print("%-8d %-8d %-10d %s" % (m, len(cs), mx, "否" if mx <= m*1.05 else "是"))

print("\n=== chunk_overlap_chars 扫描 ===")
print("%-8s %-8s %s" % ("重叠","片段数","总覆盖字数"))
for o in (0, 50, 200, 400):
    cs = chunk_text("t", long_text, max_chars=800, overlap_chars=o, min_chars=20)
    print("%-8d %-8d %d" % (o, len(cs), sum(len(c.text) for c in cs)))

print("\n=== BM25 k1 / b 扫描（前 5 名排序）===")
q = " ".join(raw.split())[:120]
def bm25_top(k1, b):
    ix = BM25Index(store, k1=k1, b=b, tokenizer_cache_dir=base.cache_dir)
    return [cid for cid,_ in ix.search(q, top_k=5)]
for k1 in (0.9, 1.2, 1.5, 2.0):
    for b in (0.3, 0.75, 0.9):
        top = bm25_top(k1, b)
        print("  k1=%-4.1f b=%-5.2f → %s" % (k1, b, [t.split("#")[-1][:8] for t in top][:5]))

print("\n=== rrf_k / default_top_k 扫描 ===")
for k in (10, 30, 60, 100):
    s2 = copy.deepcopy(base); s2.rrf_k = k
    hs = HybridSearcher(store, s2)
    r = hs.search(q, top_k=5)
    hits = r[0] if isinstance(r, tuple) else r
    print("  rrf_k=%-4d → 前3: %s" % (k, [h.chunk_id.split("#")[-1][:8] for h in hits[:3]]))
for tk in (3, 5, 10, 20):
    hs = HybridSearcher(store, base)
    r = hs.search(q, top_k=tk)
    hits = r[0] if isinstance(r, tuple) else r
    print("  top_k=%-4d → 返回 %d 条" % (tk, len(hits)))

print("\n=== 极端值 ===")
def probe(name, fn):
    try:
        out = fn()
        print("  %-34s → %s" % (name, str(out)[:60]))
    except Exception as e:
        print("  %-34s → 异常 %s: %s" % (name, type(e).__name__, str(e)[:50]))

probe("max_chars=0 切块", lambda: len(chunk_text("t", long_text, max_chars=0, overlap_chars=0, min_chars=20)))
probe("max_chars=1 切块", lambda: len(chunk_text("t", long_text, max_chars=1, overlap_chars=0, min_chars=1)))
probe("overlap>max 切块", lambda: len(chunk_text("t", long_text, max_chars=200, overlap_chars=500, min_chars=20)))
def fp0():
    from litagent.index.phrase import PhraseIndex
    return PhraseIndex(store).n
probe("指纹 n 读取", fp0)
