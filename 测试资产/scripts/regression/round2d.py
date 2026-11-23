# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys, os, shutil, sqlite3
BASE = str(_REPO_ROOT / '作品相关代码' / 'scr')
TMPDATA = str(_REPO_ROOT / '测试资产' / 'data' / 'data_copy')
if os.path.isdir(TMPDATA): shutil.rmtree(TMPDATA, ignore_errors=True)
os.makedirs(TMPDATA)
for sub in ("raw","clean","pdfs","summaries","cache"):
    os.makedirs(os.path.join(TMPDATA, sub), exist_ok=True)
shutil.copy2(os.path.join(BASE,"data","index.db"), os.path.join(TMPDATA,"index.db"))
os.environ["LIT_DATA_DIR"] = TMPDATA
sys.path.insert(0, BASE); os.chdir(BASE)
from streamlit.testing.v1 import AppTest

RES = []
def rec(name, ok, detail=""):
    RES.append(dict(name=name, ok=bool(ok), detail=str(detail)[:300]))
    print("%-6s %-40s %s" % ("PASS" if ok else "FAIL", name, str(detail)[:120]))

at = AppTest.from_file(os.path.join(BASE,"app.py"), default_timeout=180); at.run()
rec("副本数据目录启动", not at.exception)

def btns(): return [str(b.label) for b in at.button]
def find_btn(kw):
    for i,b in enumerate(at.button):
        if kw in str(b.label): return i, b
    return None, None

# 未选择文献时删除按钮应为禁用
i0, b0 = find_btn("确认删除")
rec("未选文献时删除按钮禁用", b0 is not None and getattr(b0,"disabled",False), "disabled=%s" % getattr(b0,"disabled",None))

# 选择一篇
sel = [s for s in at.selectbox if "选" in str(getattr(s,"label",""))]
rec("存在选文献下拉框", bool(sel), [getattr(s,"label","") for s in at.selectbox][:3])
c = sqlite3.connect(os.path.join(TMPDATA,"index.db"))
before = c.execute("select count(*) from documents").fetchone()[0]; c.close()

if sel:
    opts = getattr(sel[0], "options", [])
    rec("下拉框有候选项", len(opts) > 0, "共 %d 项" % len(opts))
    sel[0].set_value(opts[0]).run()
    i1, b1 = find_btn("确认删除")
    rec("选择后删除按钮可用", b1 is not None and not getattr(b1,"disabled",True), "disabled=%s" % getattr(b1,"disabled",None))
    if i1 is not None and not getattr(b1,"disabled",True):
        at.button[i1].click().run()
        rec("执行删除无异常", not at.exception, [e.value for e in at.exception][:1])
        c = sqlite3.connect(os.path.join(TMPDATA,"index.db"))
        after = c.execute("select count(*) from documents").fetchone()[0]
        orphan = c.execute("select count(*) from chunks k left join documents d on d.id=k.doc_id where d.id is null").fetchone()[0]
        ok_integrity = c.execute("pragma integrity_check").fetchone()[0]
        c.close()
        rec("删除后文献数减一", after == before-1, "%d→%d" % (before, after))
        rec("删除后无孤儿片段", orphan == 0, orphan)
        rec("删除后数据库完整", ok_integrity == "ok", ok_integrity)

fails = [r for r in RES if not r["ok"]]
print("\n== 文献库页签：%d 项，失败 %d ==" % (len(RES), len(fails)))
for f in fails: print("  FAIL:", f["name"], f["detail"][:130])
shutil.rmtree(TMPDATA, ignore_errors=True)
