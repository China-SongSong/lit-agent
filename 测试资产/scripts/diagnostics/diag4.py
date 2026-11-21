# -*- coding: utf-8 -*-
import sys, sqlite3, re
sys.path.insert(0, r"C:\Users\Lenovo\Desktop\lit_agent\作品相关代码\scr")
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.retrieve.trace import QuoteTracer

s = get_settings(); store = Store(s.db_path); store.init_schema()
tracer = QuoteTracer(store, s)
con = sqlite3.connect(str(s.db_path)); cur = con.cursor()
cur.execute("""select c.id, t.text from chunks c join chunk_text t on t.chunk_id=c.id
               join documents d on d.id=c.doc_id where d.has_fulltext=1 and length(t.text)>1500 limit 8""")
rows = cur.fetchall()

def longest_plain(raw, minlen=220):
    best = None
    for m in re.finditer(r"[^\n]{220,}", raw):
        if best is None or len(m.group()) > len(best.group()):
            best = m
    return best

print("=== DEF-009：查询仅后半段命中 ===")
checked = 0; problems = []; skipped = 0
for cid, raw in rows:
    mt0 = longest_plain(raw)
    if not mt0: skipped += 1; continue
    body = mt0.group()[:220]
    q = "unrelated filler text before " + body
    r, rp = tracer.trace(q, allow_online=False)
    if not r.found or not r.matched_text:
        skipped += 1
        continue
    checked += 1
    mt = r.matched_text; cb = r.context_before or ""; ca = r.context_after or ""
    idx = raw.find(mt)
    if idx < 0:
        problems.append(("matched_text 不在原文", repr(mt[:50]))); continue
    ok_b = (cb == "") or raw[max(0, idx-len(cb)):idx] == cb
    ok_a = (ca == "") or raw[idx+len(mt):idx+len(mt)+len(ca)] == ca
    if not (ok_b and ok_a):
        problems.append(("上下文不衔接", "idx=%d ok_b=%s ok_a=%s\n      原文前=%r\n      cb尾 =%r\n      原文后=%r\n      ca头 =%r"
                         % (idx, ok_b, ok_a, raw[max(0,idx-40):idx], cb[-40:],
                            raw[idx+len(mt):idx+len(mt)+40], ca[:40])))

print("  比对 %d 段，跳过 %d 段，问题 %d 处" % (checked, skipped, len(problems)))
for p in problems[:3]: print("   ", p[0], "|", p[1])
if checked and not problems:
    print("  结论：matched_text 与前后上下文在原文中逐字符衔接，未发现偏移")
