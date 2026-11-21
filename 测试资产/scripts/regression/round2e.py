# -*- coding: utf-8 -*-
import sys, os, shutil, sqlite3
BASE = r"C:\Users\Lenovo\Desktop\lit_agent\作品相关代码\scr"
TMPDATA = r"C:/Users/Lenovo/Desktop/lit_agent/测试资产/data\data_copy"
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
def find_btn(kw):
    for i,b in enumerate(at.button):
        if kw in str(b.label): return i,b
    return None,None
def find_cb(kw):
    for i,cb in enumerate(at.checkbox):
        if kw in str(getattr(cb,"label","")): return i,cb
    return None,None

i0,b0 = find_btn("确认删除")
rec("未勾选确认时删除按钮禁用", b0 is not None and getattr(b0,"disabled",False))

c = sqlite3.connect(os.path.join(TMPDATA,"index.db")); before = c.execute("select count(*) from documents").fetchone()[0]; c.close()
sel = [s for s in at.selectbox if "选" in str(getattr(s,"label",""))]
opts = getattr(sel[0],"options",[])
sel[0].set_value(opts[0]).run()
rec("选定文献后出现确认勾选框", find_cb("我确认删除")[0] is not None,
    [getattr(x,"label","")[:30] for x in at.checkbox][:3])

i_cb, cb = find_cb("我确认删除")
if i_cb is not None:
    at.checkbox[i_cb].check().run()
    i1,b1 = find_btn("确认删除")
    rec("勾选后删除按钮可用", b1 is not None and not getattr(b1,"disabled",True), "disabled=%s" % getattr(b1,"disabled",None))
    if i1 is not None and not getattr(b1,"disabled",True):
        at.button[i1].click().run()
        rec("执行删除无异常", not at.exception, [e.value for e in at.exception][:1])
        c = sqlite3.connect(os.path.join(TMPDATA,"index.db"))
        after = c.execute("select count(*) from documents").fetchone()[0]
        orphan = c.execute("select count(*) from chunks k left join documents d on d.id=k.doc_id where d.id is null").fetchone()[0]
        integ = c.execute("pragma integrity_check").fetchone()[0]
        c.close()
        rec("删除后文献数减一", after == before-1, "%d→%d" % (before, after))
        rec("删除后无孤儿片段", orphan == 0, orphan)
        rec("删除后数据库完整", integ == "ok", integ)

fails = [r for r in RES if not r["ok"]]
print("\n== 文献库删除流程：%d 项，失败 %d ==" % (len(RES), len(fails)))
for f in fails: print("  FAIL:", f["name"], f["detail"][:130])
shutil.rmtree(TMPDATA, ignore_errors=True)
