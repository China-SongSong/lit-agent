# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys, os, shutil, sqlite3
sys.path.insert(0, str(_REPO_ROOT / '作品相关代码' / 'scr'))
from litagent.config import get_settings
from litagent.index.store import Store
RES=[]
def rec(no, name, ok, detail=""):
    RES.append(dict(no=no, name=name, ok=bool(ok), detail=str(detail)[:300]))
    print("%-6s #%-2s %-44s %s" % ("PASS" if ok else "FAIL", no, name, str(detail)[:95]))

s = get_settings()
TMP = str(_REPO_ROOT / '测试资产' / 'data' / 'e2e.db')
for p in (TMP, TMP+"-wal", TMP+"-shm"):
    try: os.remove(p)
    except Exception: pass
src = sqlite3.connect(str(s.db_path)); dst = sqlite3.connect(TMP); src.backup(dst); dst.close(); src.close()
store = Store(TMP); store.init_schema()

# 24a 重建索引
from litagent.index.builder import IndexBuilder
before = (store.count_documents(), store.count_chunks() if hasattr(store,"count_chunks") else -1)
b = IndexBuilder(store, s)
res = b.build_all()
con = sqlite3.connect(TMP)
post = con.execute("select count(*) from postings").fetchone()[0]
fp = con.execute("select count(*) from fingerprints").fetchone()[0]
chunks = con.execute("select count(*) from chunks").fetchone()[0]
con.close()
rec("24a", "索引重建后计数一致", post>0 and fp>0 and chunks==254,
    "片段 %d、倒排 %d、指纹 %d" % (chunks, post, fp))

# 24b 总结两次：第二次应全缓存
from litagent.summarize.runner import SummaryRunner
con = sqlite3.connect(TMP)
did = con.execute("""select m.doc_id from summaries m join documents d on d.id=m.doc_id
                     where d.has_fulltext=1 limit 1""").fetchone()[0]
con.close()
r1 = SummaryRunner(store, s).run(doc_ids=[did], redo=True)
rec("24b.1", "总结首次执行完成", r1.processed>=0 and not r1.errors,
    "成功 %d 调用 %d 缓存 %d" % (r1.processed, r1.calls, r1.cache_hits))
r2 = SummaryRunner(store, s).run(doc_ids=[did], redo=True)
rec("24b.2", "总结第二次全缓存命中", r2.calls==0 and r2.cache_hits>0,
    "调用 %d 缓存 %d" % (r2.calls, r2.cache_hits))

# 24c ask
from litagent.agent.loop import LiteratureAgent
try:
    ag = LiteratureAgent(store, s)
    ans = ag.ask("库里一共收录了多少篇文献？")
    ag.close()
    rec("24c", "Agent 问答端到端可用", bool(ans) and len(ans)>5, (ans or "")[:80].replace(chr(10)," "))
except Exception as e:
    rec("24c", "Agent 问答端到端可用", False, "%s: %s" % (type(e).__name__, str(e)[:80]))

store.close()
for p in (TMP, TMP+"-wal", TMP+"-shm"):
    try: os.remove(p)
    except Exception: pass
fails=[x for x in RES if not x["ok"]]
print("\n== 端到端冒烟：%d 项，失败 %d ==" % (len(RES), len(fails)))
for f in fails: print("  FAIL #%s %s %s" % (f["no"], f["name"], f["detail"][:150]))
