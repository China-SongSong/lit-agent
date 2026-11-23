# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys, sqlite3, re, os
sys.path.insert(0, str(_REPO_ROOT / '作品相关代码' / 'scr'))
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.retrieve.trace import QuoteTracer

RES = []
def rec(item, name, ok, detail=""):
    RES.append(dict(item=item, name=name, ok=bool(ok), detail=str(detail)[:300]))
    print("%-6s [%s] %-36s %s" % ("PASS" if ok else "FAIL", item, name, str(detail)[:110]))

s = get_settings(); store = Store(s.db_path); store.init_schema()
tracer = QuoteTracer(store, s)
con = sqlite3.connect(str(s.db_path)); cur = con.cursor()

# ============ DEF-009 修正后的偏移校验 ============
cur.execute("""select t.text from chunks c join chunk_text t on t.chunk_id=c.id
               join documents d on d.id=c.doc_id where d.has_fulltext=1 and length(t.text)>1200 limit 12""")
checked = 0; bad = []
for (raw,) in cur.fetchall():
    runs = [m.group() for m in re.finditer(r"[^\n]{180,}", raw)]
    if not runs: continue
    body = max(runs, key=len)[:180]
    q = "this leading part is unrelated and should not match " + body
    r, rp = tracer.trace(q, allow_online=False)
    if not r.found or not r.matched_text: continue
    checked += 1
    mt = r.matched_text
    idx = raw.find(mt)
    if idx < 0: bad.append("matched_text 不在原文"); continue
    cb = (r.context_before or "").rstrip()
    ca = (r.context_after or "").lstrip()
    ok_b = (not cb) or raw[:idx].rstrip().endswith(cb)
    ok_a = (not ca) or raw[idx+len(mt):].lstrip().startswith(ca)
    if not (ok_b and ok_a):
        bad.append("idx=%d ok_b=%s ok_a=%s" % (idx, ok_b, ok_a))
rec("DEF-009", "仅后半段命中时上下文衔接（%d 段）" % checked, checked >= 3 and not bad, bad[:2] or "前后文与 matched_text 在原文中完全衔接")

# ============ DEF-012 PDF 文字密度 ============
import pymupdf
import litagent.clean.pdf_text as pt

def make_pdf(path, text, pages=1):
    d = pymupdf.open()
    for i in range(pages):
        p = d.new_page()
        y = 72
        for line in [text[i::pages]]:
            p.insert_text((72, y), line[:80])
            y += 14
    d.save(path); d.close()

tmpdir = str(_REPO_ROOT / '测试资产' / 'data' / 'pdfs')
os.makedirs(tmpdir, exist_ok=True)
sparse = os.path.join(tmpdir, "sparse.pdf")
dense = os.path.join(tmpdir, "dense.pdf")
make_pdf(sparse, "short text")                     # 约 10 字/页，密度极低
long_txt = "lorem ipsum dolor sit amet consectetur adipiscing elit " * 40
make_pdf(dense, long_txt)                          # 每页上千字

ex_s = pt.extract_pdf(sparse)
dens_s = len(ex_s.fulltext) / max(1, ex_s.page_count)
rec("DEF-012", "稀疏 PDF 判为疑似扫描件", pt.looks_like_scanned(ex_s), "密度 %.0f 字/页" % dens_s)
rec("DEF-012", "稀疏 PDF 记入密度告警", any("密度偏低" in e for e in ex_s.errors),
    [e[:50] for e in ex_s.errors][:2])

ex_d = pt.extract_pdf(dense)
dens_d = len(ex_d.fulltext) / max(1, ex_d.page_count)
rec("DEF-012", "正常 PDF 不判为扫描件", not pt.looks_like_scanned(ex_d), "密度 %.0f 字/页" % dens_d)
rec("DEF-012", "正常 PDF 无密度告警", not any("密度偏低" in e for e in ex_d.errors))

# 陈旧变量检验：首选方法抽得极少、降级方法抽得多，密度必须按最终结果重算
from litagent.clean.pdf_text import PdfExtraction, PageText
def mk(text, method):
    e = PdfExtraction(path="x", method=method, page_count=1)
    e.pages = [PageText(number=1, text=text)]
    return e
orig_pm, orig_pl = pt.extract_with_pymupdf, pt.extract_with_pdfminer
try:
    pt.extract_with_pymupdf = lambda p, **kw: mk("tiny", "pymupdf")
    pt.extract_with_pdfminer = lambda p: mk("A" * 5000, "pdfminer")
    ex = pt.extract_pdf(sparse)
    dens = len(ex.fulltext) / max(1, ex.page_count)
    rec("DEF-012", "降级后按最终结果重算密度", ex.method == "pdfminer" and dens >= 50 and not any("密度偏低" in e for e in ex.errors),
        "method=%s 密度=%.0f 告警=%s" % (ex.method, dens, [e[:30] for e in ex.errors]))
finally:
    pt.extract_with_pymupdf, pt.extract_with_pdfminer = orig_pm, orig_pl

bad2 = [r for r in RES if not r["ok"]]
print("\n== 本轮补测：%d 项，失败 %d ==" % (len(RES), len(bad2)))
for b in bad2: print("  FAIL:", b["item"], b["name"], b["detail"][:130])
