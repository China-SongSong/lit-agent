# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys, sqlite3, time
sys.path.insert(0, str(_REPO_ROOT / '作品相关代码' / 'scr'))
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.index.phrase import PhraseIndex, exact_tokens, exact_tokens_with_spans
s = get_settings(); store = Store(s.db_path); store.init_schema()
ph = PhraseIndex(store)
q = "alphafold pappu ruff intrinsic disorder"
toks = exact_tokens(q)
postings = store.postings_for_terms(toks, max_df_ratio=1.0)
cand = list({cid for ch in postings.values() for cid,_ in ch})
print("查询词元:", toks, "候选:", len(cand))

# 组件计时
t0 = time.perf_counter()
for _ in range(3): store.postings_for_terms(toks, max_df_ratio=1.0)
t_post = (time.perf_counter()-t0)/3*1000

texts = [store.get_chunk_text(c) or "" for c in cand]
t0 = time.perf_counter()
for t in texts: exact_tokens_with_spans(t)
t_tok = (time.perf_counter()-t0)*1000

t0 = time.perf_counter()
ph._search_short(toks, top_k=5, min_ratio=0.25, with_text=True)
t_short = (time.perf_counter()-t0)*1000

t0 = time.perf_counter()
ph.search(q, top_k=5, min_ratio=0.25, with_text=True)
t_search = (time.perf_counter()-t0)*1000

print("postings 查询      : %.2f ms" % t_post)
print("候选分词(%d 段)    : %.2f ms" % (len(texts), t_tok))
print("_search_short 整体 : %.2f ms" % t_short)
print("PhraseIndex.search : %.2f ms" % t_search)
