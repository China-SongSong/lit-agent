# -*- coding: utf-8 -*-
import sys, sqlite3
sys.path.insert(0, r"C:\Users\Lenovo\Desktop\lit_agent\作品相关代码\scr")
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.retrieve.trace import QuoteTracer
import json
s = get_settings(); store = Store(s.db_path); store.init_schema()
tr = QuoteTracer(store, s)
con = sqlite3.connect(str(s.db_path))

raw = con.execute("""select t.text from chunks c join chunk_text t on t.chunk_id=c.id
   join documents d on d.id=c.doc_id where d.has_fulltext=1 and length(t.text)>1200 limit 1""").fetchone()[0]
q = " ".join(raw.split())
base = q[100:320]
print("基线片段:", base[:120])
print()

# 在切片内部替换一个词
import re
m = re.search(r"\b(the|and|of|was|were|with)\b", base)
print("切片内找到可替换词:", m.group(0) if m else "无", "位置", m.start() if m else -1)
variants = [("原文", base)]
if m:
    rep = {"the":"a","and":"plus","of":"for","was":"were","were":"was","with":"by"}[m.group(0)]
    variants.append(("改一个词", base[:m.start()] + rep + base[m.end():]))
# 删掉 10% 的词
w = base.split(); w2 = [x for i,x in enumerate(w) if i % 10 != 3]
variants.append(("删约10%词", " ".join(w2)))

print("%-12s %-14s %-9s %s" % ("变体","等级","置信度","找到"))
for name, v in variants:
    r,_ = tr.trace(v, allow_online=False)
    print("%-12s %-14s %-9.4f %s" % (name, r.level, r.confidence, r.found))

print("\n=== j.apenergy 总结内容 ===")
row = con.execute("select tldr,problem,methods,findings,contributions,limitations,claims_json,created_at from summaries where doc_id=?",
                  ("doi:10.1016/j.apenergy.2026.128706",)).fetchone()
if row:
    for k,v in zip(("tldr","problem","methods","findings","contributions","limitations"), row[:6]):
        print("  %-14s %r" % (k, (v or "")[:40]))
    cl = json.loads(row[6] or "[]")
    print("  claims=%d  created_at=%s" % (len(cl), row[7]))
c.close()
