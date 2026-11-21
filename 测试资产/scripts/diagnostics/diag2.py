# -*- coding: utf-8 -*-
import sys, sqlite3, re
sys.path.insert(0, r"C:\Users\Lenovo\Desktop\lit_agent\作品相关代码\scr")
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.retrieve.trace import QuoteTracer
from litagent.schema import normalize_whitespace

s = get_settings(); store = Store(s.db_path); store.init_schema()
tracer = QuoteTracer(store, s)
con = sqlite3.connect(str(s.db_path)); cur = con.cursor()

print("=== DEF-009 诊断 ===")
cur.execute("""select t.text from chunks c join chunk_text t on t.chunk_id=c.id
               join documents d on d.id=c.doc_id where d.has_fulltext=1 and length(t.text)>1400 limit 3""")
for i,(raw,) in enumerate(cur.fetchall(),1):
    orig = normalize_whitespace(raw)
    q = orig[300:520]
    r, rp = tracer.trace(q, allow_online=False)
    if not r.found: print("  第%d段 未命中" % i); continue
    m = normalize_whitespace(r.matched_text)
    cb = normalize_whitespace(r.context_before or "")
    ca = normalize_whitespace(r.context_after or "")
    joined = cb + m + ca
    ok_join = joined in orig
    first = orig.find(m)
    print("  第%d段: len(m)=%d 出现位置=%s 拼接后是原文字串=%s" % (i, len(m), first, ok_join))
    if not ok_join:
        # 找到拼接失败的偏移
        j = orig.find(m)
        print("      原文 idx=%d 前后: ...%s|[%s]|%s..." % (j, orig[max(0,j-40):j], m[:30], orig[j+len(m):j+len(m)+40]))
        print("      cb 尾部: %r" % cb[-50:])
        print("      ca 头部: %r" % ca[:50])
        print("      cb 是否为原文前缀结尾:", orig[:j].endswith(cb[-40:]))

print("\n=== DEF-010 调参找 local_weak ===")
cur.execute("select t.text from chunks c join chunk_text t on t.chunk_id=c.id join documents d on d.id=c.doc_id where d.has_fulltext=1 and length(t.text)>900 limit 1")
base = " ".join(cur.fetchone()[0].split())[100:300]
for k in (16, 20, 24, 28, 32, 36, 40, 44):
    q = base + " " + " ".join(["furthermore"] * k)
    r, rp = tracer.trace(q, allow_online=False)
    print("  pad=%-3d level=%-12s conf=%.3f tokens=%d" % (k, r.level, r.confidence, rp.quote_tokens))
