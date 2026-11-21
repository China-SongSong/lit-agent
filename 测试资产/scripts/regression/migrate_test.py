# -*- coding: utf-8 -*-
import sys, os, sqlite3, shutil, time
sys.path.insert(0, r"C:\Users\Lenovo\Desktop\lit_agent\作品相关代码\scr")
from litagent.config import get_settings
from litagent.index.store import Store

RES = []
def rec(name, ok, detail=""):
    RES.append(dict(name=name, ok=bool(ok), detail=str(detail)[:300]))
    print("%-6s %-38s %s" % ("PASS" if ok else "FAIL", name, str(detail)[:110]))

s = get_settings()
OLD = r"C:\Users\Lenovo\Desktop\lit_agent\备份\index-before-summary-regeneration-20261030-204209.db"
TMP = r"C:/Users/Lenovo/Desktop/lit_agent/测试资产/data\migrate_test.db"
for p in (TMP, TMP+"-wal", TMP+"-shm"):
    try: os.remove(p)
    except Exception: pass
shutil.copy2(OLD, TMP)

c = sqlite3.connect(TMP)
cols_before = [r[1] for r in c.execute("PRAGMA table_info(chunk_text)").fetchall()]
docs_before = c.execute("select count(*) from documents").fetchone()[0]
chunks_before = c.execute("select count(*) from chunks").fetchone()[0]
c.close()
rec("旧库无 normalized_text 列", "normalized_text" not in cols_before, cols_before)

t0 = time.time()
st = Store(TMP); st.init_schema()
dt = time.time() - t0
c = sqlite3.connect(TMP)
cols_after = [r[1] for r in c.execute("PRAGMA table_info(chunk_text)").fetchall()]
has_col = "normalized_text" in cols_after
filled = c.execute("select count(*) from chunk_text where normalized_text is not null and normalized_text != ''").fetchone()[0]
total = c.execute("select count(*) from chunk_text").fetchone()[0]
docs_after = c.execute("select count(*) from documents").fetchone()[0]
chunks_after = c.execute("select count(*) from chunks").fetchone()[0]
integ = c.execute("pragma integrity_check").fetchone()[0]
c.close()
rec("迁移后新增 normalized_text 列", has_col, cols_after)
rec("normalized_text 已回填", filled == total and total > 0, "%d/%d" % (filled, total))
rec("迁移不改变文献与片段数", docs_after == docs_before and chunks_after == chunks_before,
    "文献 %d->%d 片段 %d->%d" % (docs_before, docs_after, chunks_before, chunks_after))
rec("迁移后完整性 ok", integ == "ok", integ)
rec("迁移耗时合理", dt < 60, "%.1f 秒" % dt)

from litagent.retrieve.trace import QuoteTracer
tr = QuoteTracer(st, s)
c = sqlite3.connect(TMP)
raw = c.execute("select text from chunk_text where length(text)>800 limit 1").fetchone()[0]
c.close()
r, rp = tr.trace(" ".join(raw.split())[50:200], allow_online=False)
rec("迁移后溯源可用", isinstance(r.level, str) and r.level != "", "level=%s conf=%.3f" % (r.level, r.confidence))
st.close()
for p in (TMP, TMP+"-wal", TMP+"-shm"):
    try: os.remove(p)
    except Exception: pass

fails = [x for x in RES if not x["ok"]]
print("\n== 迁移：%d 项，失败 %d ==" % (len(RES), len(fails)))
for f in fails: print("  FAIL:", f["name"], f["detail"][:130])
