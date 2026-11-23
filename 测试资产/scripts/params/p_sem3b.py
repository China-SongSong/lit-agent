# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys, sqlite3, re, random
sys.path.insert(0, str(_REPO_ROOT / '作品相关代码' / 'scr'))
from litagent.config import get_settings
from litagent.index.store import Store
import litagent.retrieve.trace as T
base = get_settings(); store = Store(base.db_path); store.init_schema()
con = sqlite3.connect(str(base.db_path))
tr = T.QuoteTracer(store, base)

# 每个全文文献取 1 段，确保跨文献
docs = [r[0] for r in con.execute("select id from documents where has_fulltext=1")]
rows = []
for did in docs:
    r = con.execute("""select t.text from chunks c join chunk_text t on t.chunk_id=c.id
                       where c.doc_id=? and length(t.text)>900 limit 1""", (did,)).fetchone()
    if r: rows.append((did, r[0]))
print("跨文献样本: %d 篇" % len(rows))

SYN = {"significant":"notable","method":"approach","results":"findings","study":"work",
       "analysis":"examination","propose":"introduce","show":"demonstrate","data":"dataset"}
def reword(t, drop=0.0, seed=0):
    w = t.split()
    out = []
    for i, x in enumerate(w):
        k = re.sub(r"[^a-zA-Z]","",x).lower()
        if k in SYN: out.append(x.replace(x.strip(",.()"), SYN[k]))
        else: out.append(x)
    if drop > 0:
        random.seed(seed)
        out = [x for i,x in enumerate(out) if random.random() > drop]
    return " ".join(out)

res = {"verbatim":[], "light":[], "heavy":[]}
wrong = []
for did, txt in rows:
    t = " ".join(txt.split())
    q = t[120:320]
    for name, qq in [("verbatim", q), ("light", reword(q, 0.0)), ("heavy", reword(q, 0.30, 7))]:
        r,_ = tr.trace(qq, allow_online=False)
        res[name].append((did, r.found, r.hit.doc_id if r.hit else None, r.confidence))
        if r.found and r.hit and r.hit.doc_id != did:
            wrong.append((name, did[:26], r.hit.doc_id[:26], round(r.confidence,3)))

print("\n%-10s %-8s %-10s %s" % ("形态","样本","命中数","错配数"))
for k in ("verbatim","light","heavy"):
    hit = sum(1 for x in res[k] if x[1])
    mis = sum(1 for x in res[k] if x[1] and x[2] != x[0])
    print("%-10s %-8d %-10d %d" % (k, len(res[k]), hit, mis))
if wrong:
    print("\n错配明细:")
    for w in wrong[:8]: print("  ", w)

print("\n=== 嵌合查询（A 段 + B 段，跨文献）===")
if len(rows) >= 2:
    a = " ".join(rows[0][1].split())[120:300]
    b = " ".join(rows[1][1].split())[120:300]
    r, rp = tr.trace(a + " " + b, allow_online=False)
    print("  归属: %s" % (r.hit.doc_id[:46] if r.hit else "无"))
    print("  A=%s" % rows[0][0][:46])
    print("  B=%s" % rows[1][0][:46])
    print("  level=%s conf=%.3f 跨片段=%d 候选=%d" % (r.level, r.confidence, rp.spanned_chunks, len(r.candidates)))
