# -*- coding: utf-8 -*-
import sys, os, sqlite3
sys.path.insert(0, "C:/Users/Lenovo/Desktop/lit_agent/作品相关代码/scr")
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.summarize.runner import SummaryRunner
RES=[]
def rec(no, name, ok, detail=""):
    RES.append(dict(no=no, name=name, ok=bool(ok), detail=str(detail)[:300]))
    print("%-6s #%-2s %-44s %s" % ("PASS" if ok else "FAIL", no, name, str(detail)[:95]))

s = get_settings()
TMP = "C:/Users/Lenovo/Desktop/lit_agent/测试资产/data/e2e2.db"
for p in (TMP, TMP+"-wal", TMP+"-shm"):
    try: os.remove(p)
    except Exception: pass
src = sqlite3.connect(str(s.db_path)); dst = sqlite3.connect(TMP); src.backup(dst); dst.close(); src.close()
store = Store(TMP); store.init_schema()
con = sqlite3.connect(TMP)
did = con.execute("""select m.doc_id from summaries m join documents d on d.id=m.doc_id
                     where d.has_fulltext=1 limit 1""").fetchone()[0]
con.execute("delete from summaries where doc_id=?", (did,)); con.commit(); con.close()

r1 = SummaryRunner(store, s).run(doc_ids=[did])
rec("24b.1", "首次总结产生调用", r1.calls>0 and not r1.errors, "调用 %d 缓存 %d 成功 %d" % (r1.calls, r1.cache_hits, r1.processed))
r2 = SummaryRunner(store, s).run(doc_ids=[did])
rec("24b.2", "第二次不产生任何模型调用", r2.calls==0,
    "调用 %d 缓存 %d 跳过 %d" % (r2.calls, r2.cache_hits, r2.skipped))

# 再验缓存路径：删掉总结但保留 llm_cache，redo=False 时应命中缓存
con = sqlite3.connect(TMP); con.execute("delete from summaries where doc_id=?", (did,)); con.commit(); con.close()
r3 = SummaryRunner(store, s).run(doc_ids=[did])
rec("24b.3", "删除总结后重跑命中模型缓存", r3.calls==0 or r3.cache_hits>0,
    "调用 %d 缓存 %d" % (r3.calls, r3.cache_hits))

store.close()
for p in (TMP, TMP+"-wal", TMP+"-shm"):
    try: os.remove(p)
    except Exception: pass
fails=[x for x in RES if not x["ok"]]
print("\n== 总结缓存：%d 项，失败 %d ==" % (len(RES), len(fails)))
for f in fails: print("  FAIL #%s %s %s" % (f["no"], f["name"], f["detail"][:150]))
