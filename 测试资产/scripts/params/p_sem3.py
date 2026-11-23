# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys, sqlite3, random, re
sys.path.insert(0, str(_REPO_ROOT / '作品相关代码' / 'scr'))
from litagent.config import get_settings
from litagent.index.store import Store
import litagent.retrieve.trace as T
base = get_settings(); store = Store(base.db_path); store.init_schema()
con = sqlite3.connect(str(base.db_path))
tr = T.QuoteTracer(store, base)

rows = list(con.execute("""select c.doc_id, t.text from chunks c join chunk_text t on t.chunk_id=c.id
        join documents d on d.id=c.doc_id where d.has_fulltext=1 and length(t.text)>900 limit 10"""))
print("样本文献 %d 篇" % len({r[0] for r in rows}))

SYN = {"significant":"notable","method":"approach","results":"findings","study":"work",
       "analysis":"examination","propose":"introduce","show":"demonstrate","data":"dataset",
       "increase":"rise","important":"crucial","different":"various","paper":"article"}
def reword(t, level=1):
    w = t.split()
    for i,x in enumerate(w):
        k = re.sub(r"[^a-zA-Z]","",x).lower()
        if k in SYN and (level>=2 or i % 2 == 0):
            w[i] = x.replace(x.strip(",.()"), SYN[k])
    return " ".join(w)

stats = {"verbatim":0, "reword1":0, "reword2":0, "wrong_doc":0, "total":0, "found_wrong":0}
details = []
for did, txt in rows:
    t = " ".join(txt.split())
    q = t[120:320]
    for name, qq in [("verbatim", q), ("reword1", reword(q,1)), ("reword2", reword(q,2))]:
        r,_ = tr.trace(qq, allow_online=False)
        stats["total"] += 1
        if r.found:
            stats[name] += 1
            got = r.hit.doc_id if r.hit else None
            if got != did:
                stats["wrong_doc"] += 1
                details.append((name, did[:28], (got or "")[:28], round(r.confidence,3)))

print("\n=== 改写后的命中与错配 ===")
print("  逐字原文命中: %d" % stats["verbatim"])
print("  轻度改写命中: %d" % stats["reword1"])
print("  重度改写命中: %d" % stats["reword2"])
print("  总样本: %d" % stats["total"])
print("  命中但归属到错误文献: %d" % stats["wrong_doc"])
for d in details[:6]: print("   ", d)

# 嵌合样本：A 的片段 + B 的片段
print("\n=== 嵌合查询（A 前段 + B 后段）===")
if len(rows) >= 2:
    a = " ".join(rows[0][1].split())[100:300]
    b = " ".join(rows[1][1].split())[100:300]
    chim = a + " " + b
    r, rp = tr.trace(chim, allow_online=False)
    print("  命中文献: %s" % (r.hit.doc_id[:40] if r.hit else "无"))
    print("  等级=%s 置信度=%.3f 跨片段=%d" % (r.level, r.confidence, rp.spanned_chunks))
    print("  A=%s" % rows[0][0][:40])
    print("  B=%s" % rows[1][0][:40])
