# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys, re, sqlite3, dataclasses
sys.path.insert(0, str(_REPO_ROOT / '作品相关代码' / 'scr'))
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.retrieve.trace import QuoteTracer
from litagent.cite.gbt7714 import format_gbt7714
from litagent.schema import TraceResult

s = get_settings(); store = Store(s.db_path); tracer = QuoteTracer(store, s)

print("=== TraceResult.level 默认值 ===")
for f in dataclasses.fields(TraceResult):
    if f.name in ("level", "found", "note"):
        print("  %-8s default=%r" % (f.name, f.default))
r, rep = tracer.trace("", allow_online=False)
print("  空输入实际: level=%r found=%r note=%r" % (r.level, r.found, r.note))

print("\n=== HTML 标签进入引文（Issues.md #5）===")
con = sqlite3.connect(str(s.db_path))
did = con.execute("select id from documents limit 1").fetchone()[0]
doc = store.get_document(did, with_fulltext=False)
dd = doc.to_dict() if hasattr(doc, "to_dict") else dict(doc.__dict__)
import copy
for tag in ["<i>Zhang</i>", "<b>Zhang</b>", "Zhang<sup>1</sup>", "&amp;Zhang"]:
    d2 = copy.deepcopy(dd)
    d2["authors"][0]["family"] = tag
    out = format_gbt7714(d2)
    has_tag = bool(re.search(r"</?[a-zA-Z][^>]*>", out))
    print("  %-22s → 引文含标签=%s | %s" % (tag, has_tag, out[:70]))

print("\n=== 真实语料里作者是否含标签 ===")
n = 0
for (aid,) in con.execute("select id from documents"):
    d = store.get_document(aid, with_fulltext=False)
    txt = " ".join("%s %s" % (a.family or "", a.given or "") for a in (d.authors if d else []))
    if re.search(r"</?[a-zA-Z][^>]*>|&[a-z]+;", txt):
        n += 1
        if n <= 3: print("  含标签:", aid, "|", txt[:80])
print("  合计含标签文献:", n)
