# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys, sqlite3, re
sys.path.insert(0, str(_REPO_ROOT / '作品相关代码' / 'scr'))
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.retrieve.trace import QuoteTracer
s = get_settings(); store = Store(s.db_path); store.init_schema()
tr = QuoteTracer(store, s)
con = sqlite3.connect(str(s.db_path))

# 找一个确实包含可替换词的片段
cand = None
for (txt,) in con.execute("""select t.text from chunks c join chunk_text t on t.chunk_id=c.id
        join documents d on d.id=c.doc_id where d.has_fulltext=1 and length(t.text)>1200"""):
    q = " ".join(txt.split())
    for w in (" the "," and "," of "," was "," were "):
        i = q.find(w)
        if i > 100:
            cand = (q, w, i); break
    if cand: break
q, w, i = cand
q_mod = q[:i] + (" a " if w.strip()=="the" else " plus ") + q[i+len(w):]
print("原查询片段:", q[i-50:i+60].replace("\n"," "))
print("改后片段  :", q_mod[i-50:i+70].replace("\n"," "))
r1,_ = tr.trace(q[100:320], allow_online=False)
r2,_ = tr.trace(q_mod[100:320], allow_online=False)
print("\n原文  : level=%s conf=%.4f found=%s" % (r1.level, r1.confidence, r1.found))
print("改一词: level=%s conf=%.4f found=%s" % (r2.level, r2.confidence, r2.found))
print("结论是否不同:", (r1.level, round(r1.confidence,4)) != (r2.level, round(r2.confidence,4)))

print("\n=== 2026-11-18 生成的总结 ===")
row = con.execute("""select doc_id, created_at, tldr, problem, methods, findings, contributions, limitations
                     from summaries where created_at like '2026-11-18%'""").fetchone()
if row:
    n = sum(1 for x in row[2:] if x and str(x).strip())
    print("  %s  非空字段=%d/6  tldr=%r" % (row[0][:44], n, (row[2] or "")[:50]))
else:
    print("  无")
print("\n=== 全部总结完整度 ===")
for r in con.execute("select doc_id,tldr,problem,methods,findings,contributions,limitations from summaries"):
    n = sum(1 for x in r[1:] if x and str(x).strip())
    print("  %-46s %d/6" % (r[0][:44], n))
c.close()
