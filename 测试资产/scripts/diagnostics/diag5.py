# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys, sqlite3, re
sys.path.insert(0, str(_REPO_ROOT / '作品相关代码' / 'scr'))
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.retrieve.trace import QuoteTracer

s = get_settings(); store = Store(s.db_path); store.init_schema()
tracer = QuoteTracer(store, s)
con = sqlite3.connect(str(s.db_path)); cur = con.cursor()
cur.execute("""select c.id, t.text from chunks c join chunk_text t on t.chunk_id=c.id
               join documents d on d.id=c.doc_id where d.has_fulltext=1 and length(t.text)>1200 limit 12""")
rows = cur.fetchall()

checked = 0; bad = []
for cid, raw in rows:
    runs = [m.group() for m in re.finditer(r"[^\n]{180,}", raw)]
    if not runs: continue
    body = max(runs, key=len)[:180]
    q = ("this leading part is unrelated and should not match " + body).strip()
    r, rp = tracer.trace(q, allow_online=False)
    if not r.found or not r.matched_text: continue
    checked += 1
    mt = r.matched_text
    cb = (r.context_before or "").rstrip()
    ca = (r.context_after or "").lstrip()
    idx = raw.find(mt)
    if idx < 0:
        bad.append(("matched_text 不在原文", repr(mt[:40]))); continue
    ok_b = (cb == "") or raw[:idx].endswith(cb)
    ok_a = (ca == "") or raw[idx+len(mt):].startswith(ca)
    if not (ok_b and ok_a):
        bad.append(("偏移", "idx=%d ok_b=%s ok_a=%s | 前=%r cb=%r | 后=%r ca=%r"
                    % (idx, ok_b, ok_a, raw[max(0,idx-30):idx], cb[-30:],
                       raw[idx+len(mt):idx+len(mt)+30], ca[:30])))

print("=== DEF-009 结论 ===")
print("参与比对 %d 段，异常 %d 处" % (checked, len(bad)))
for b in bad[:3]: print("  ", b[0], "|", b[1])
if checked and not bad:
    print("  matched_text 在原文中定位正确，前文是其真实后缀、后文是其真实前缀 → 无偏移")
