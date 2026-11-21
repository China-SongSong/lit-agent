# -*- coding: utf-8 -*-
import sys, sqlite3, re
sys.path.insert(0, r"C:\Users\Lenovo\Desktop\lit_agent\作品相关代码\scr")
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.retrieve.trace import QuoteTracer

s = get_settings(); store = Store(s.db_path); store.init_schema()
tracer = QuoteTracer(store, s)
con = sqlite3.connect(str(s.db_path)); cur = con.cursor()

print("=== DEF-009：查询仅后半段命中时的字符偏移 ===")
cur.execute("""select c.id, t.text from chunks c join chunk_text t on t.chunk_id=c.id
               join documents d on d.id=c.doc_id where d.has_fulltext=1 and length(t.text)>1500 limit 6""")
rows = cur.fetchall()
checked = 0; problems = []
for cid, raw in rows:
    # 找一段不含换行的窗口，直接在原文上做逐字符比对
    m0 = None
    for start in range(0, max(0, len(raw)-700), 50):
        win = raw[start:start+600]
        if "\n" in win or len(win) < 600: continue
        m0 = (start, win); break
    if not m0: continue
    start, win = m0
    body = win[150:400]                      # 原文中的一段（无换行）
    q = "unrelated filler text before the match " + body   # 前半段无关，后半段是原文
    r, rp = tracer.trace(q, allow_online=False)
    if not r.found or not r.matched_text: continue
    checked += 1
    mt = r.matched_text
    cb = r.context_before or ""
    ca = r.context_after or ""
    idx = raw.find(mt)
    if idx < 0:
        problems.append(("matched_text 不在原文", mt[:40])); continue
    ok_b = (cb == "") or raw[max(0, idx-len(cb)):idx] == cb
    ok_a = (ca == "") or raw[idx+len(mt):idx+len(mt)+len(ca)] == ca
    if not (ok_b and ok_a):
        problems.append(("上下文与 matched_text 不衔接",
                         "idx=%d len(m)=%d ok_b=%s ok_a=%s | raw前=%r | cb尾=%r | raw后=%r | ca头=%r"
                         % (idx, len(mt), ok_b, ok_a,
                            raw[max(0,idx-30):idx], cb[-30:],
                            raw[idx+len(mt):idx+len(mt)+30], ca[:30])))

print("  参与比对 %d 段，问题 %d 处" % (checked, len(problems)))
for p in problems[:3]: print("   ", p[0], "|", p[1])

print("\n=== DEF-010：局部弱命中时的联网候选（pad=32）===")
cur.execute("select t.text from chunks c join chunk_text t on t.chunk_id=c.id join documents d on d.id=c.doc_id where d.has_fulltext=1 and length(t.text)>900 limit 1")
base = " ".join(cur.fetchone()[0].split())[100:300]
q = base + " " + " ".join(["furthermore"] * 32)
r, rp = tracer.trace(q, allow_online=True)
print("  level=%s conf=%.3f found=%s" % (r.level, r.confidence, r.found))
print("  联网已发起=%s 候选数=%d" % (rp.online_queried, len(r.online_candidates)))
for c in r.online_candidates[:3]:
    print("    -", (c.title if hasattr(c,'title') else str(c))[:60])
print("  错误信息:", (rp.online_error or "无")[:100])
