# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys, sqlite3, subprocess
sys.path.insert(0, str(_REPO_ROOT / '作品相关代码' / 'scr'))
from litagent.config import get_settings
from litagent.schema import Summary, Document, Claim, summary_quality
RES=[]
def rec(no, name, ok, detail=""):
    RES.append(dict(no=no, name=name, ok=bool(ok), detail=str(detail)[:300]))
    print("%-6s #%-3s %-40s %s" % ("PASS" if ok else "FAIL", no, name, str(detail)[:95]))

def mk_sum(**kw):
    s = Summary(doc_id="d")
    for k,v in kw.items(): setattr(s,k,v)
    return s

full = mk_sum(tldr="a",problem="b",methods="c",findings="d",contributions="e",limitations="f")
empty = mk_sum()
partial = mk_sum(tldr="a")

d_title = Document(id="x", title="t"); d_title.extra = {"content_level":"title"}
d_abs = Document(id="y", title="t"); d_abs.extra = {"content_level":"abstract"}

cases = [
 ("完整", full, None, None, "complete"),
 ("题录级", empty, d_title, None, "title_limited"),
 ("摘要级", partial, d_abs, None, "abstract_limited"),
 ("正文过短", empty, None, 127, "short_content_limited"),
]
for name, sumv, doc, chars, exp in cases:
    got, missing = summary_quality(sumv, doc, content_chars=chars)
    rec("A", "summary_quality %s" % name, got==exp, "%s（缺 %d 项）" % (got, len(missing)))

claims_only = mk_sum(); claims_only.claims=[Claim(text="x")]
got,_ = summary_quality(claims_only, None, content_chars=2000)
rec("B", "仅论断分类", got=="claims_only", got)

# 网页端是否接入
import re
src = open(str(_REPO_ROOT / '作品相关代码' / 'scr' / 'app.py'), encoding="utf-8").read()
rec("C", "app.py 使用 summary_quality", "summary_quality" in src, "出现 %d 次" % src.count("summary_quality"))
rec("D", "app.py 传入正文字数", "content_chars" in src, "出现 %d 次" % src.count("content_chars"))

# 自带测试
out = subprocess.run([sys.executable,"-B","-m","unittest","discover","-s","tests"],
                     cwd=str(_REPO_ROOT / '作品相关代码' / 'scr'),
                     capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
tail = (out.stderr or "").strip().splitlines()[-3:]
rec("E", "自带测试全通过", out.returncode==0, " | ".join(tail)[:90])

fails=[x for x in RES if not x["ok"]]
print("\n== 复核：%d 项，失败 %d ==" % (len(RES), len(fails)))
for f in fails: print("  FAIL #%s %s %s" % (f["no"], f["name"], f["detail"][:140]))
