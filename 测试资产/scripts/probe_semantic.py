# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, "C:/Users/Lenovo/Desktop/lit_agent/作品相关代码/scr")
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.retrieve.trace import QuoteTracer
s = get_settings(); store = Store(s.db_path); store.init_schema()
tr = QuoteTracer(store, s)
cases = [
 ("库外中文", "量子纠缠态在超导约瑟夫森结中的退相干时间测量方法研究"),
 ("库外英文", "the phonological loop in working memory revisited"),
 ("库内主题近似", "锂离子电池热失控的机理与防护措施"),
]
for name, q in cases:
    r, rp = tr.trace(q, allow_online=False)
    print("%-12s level=%-16s conf=%.4f found=%s basis=%s 候选=%d" % (
        name, r.level, r.confidence, r.found, getattr(r,"decision_basis",None), len(r.candidates or [])))
    if r.candidates:
        c0 = r.candidates[0]
        print("     首位: %s score=%.3f level=%s" % (getattr(c0,"doc_id",None), getattr(c0,"score",-1), getattr(c0,"level","")))
import litagent.schema as S
print("\n等级常量:", [x for x in dir(S) if "LEVEL" in x.upper()])
