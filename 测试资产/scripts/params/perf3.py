# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys, sqlite3, time, statistics
sys.path.insert(0, str(_REPO_ROOT / '作品相关代码' / 'scr'))
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.index.phrase import exact_tokens
from litagent.retrieve.trace import QuoteTracer
s = get_settings(); store = Store(s.db_path); store.init_schema()
tr = QuoteTracer(store, s)
con = sqlite3.connect(str(s.db_path))
raw = " ".join(con.execute("""select t.text from chunks c join chunk_text t on t.chunk_id=c.id
   join documents d on d.id=c.doc_id where d.has_fulltext=1 and length(t.text)>1200 limit 1""").fetchone()[0].split())
variants = {
 "常见词(6)": "the of and in is to that",
 "混合(6)": "introduction modern science citation",
 "专名(6)": "alphafold pappu ruff intrinsic disorder",
 "专名(9)": "alphafold pappu ruff intrinsic disorder deepmind protein folding",
 "原文片段(10)": raw[:80],
}
print("%-14s %-7s %-9s %-11s %s" % ("查询", "词元", "候选片段", "中位耗时ms", "路径"))
for name, q in variants.items():
    toks = exact_tokens(q)
    postings = store.postings_for_terms(toks, max_df_ratio=1.0)
    cand = len({cid for ch in postings.values() for cid,_ in ch})
    ts = []
    for _ in range(6):
        t0 = time.perf_counter(); tr.trace(q, allow_online=False); ts.append((time.perf_counter()-t0)*1000)
    path = "短查询退化路径" if len(toks) < 8 else "指纹路径"
    print("%-14s %-7d %-9d %-11.2f %s" % (name, len(toks), cand, statistics.median(ts), path))

# 最终数据核对
print("\n最终数据: 文献=%d 片段=%d 指纹=%d 总结=%d 完整性=%s" % (
    con.execute("select count(*) from documents").fetchone()[0],
    con.execute("select count(*) from chunks").fetchone()[0],
    con.execute("select count(*) from fingerprints").fetchone()[0],
    con.execute("select count(*) from summaries").fetchone()[0],
    con.execute("pragma integrity_check").fetchone()[0]))
