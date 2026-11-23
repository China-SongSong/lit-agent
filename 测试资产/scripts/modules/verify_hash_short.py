# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys, os, sqlite3, shutil, time, statistics
sys.path.insert(0, str(_REPO_ROOT / '作品相关代码' / 'scr'))
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.retrieve.trace import QuoteTracer
from litagent.evaluation.corpus_build import logical_database_sha256
RES=[]
def rec(name, ok, detail=""):
    RES.append(dict(name=name, ok=bool(ok), detail=str(detail)[:260]))
    print("%-6s %-40s %s" % ("PASS" if ok else "FAIL", name, str(detail)[:110]))

s = get_settings()
TMP = str(_REPO_ROOT / '测试资产' / 'data' / 'hash_copy.db')
for p in (TMP,TMP+"-wal",TMP+"-shm"):
    try: os.remove(p)
    except Exception: pass
shutil.copy2(str(s.db_path), TMP)
h0 = logical_database_sha256(TMP)

c = sqlite3.connect(TMP)
c.execute("update summaries set tldr='__changed__' where rowid=1")
c.execute("update meta set value='999' where key='schema_version'")
c.commit(); c.close()
h1 = logical_database_sha256(TMP)
rec("总结/元数据变更不影响语料哈希", h0 == h1, "%s vs %s" % (h0[:12], h1[:12]))

c = sqlite3.connect(TMP)
row = c.execute("select chunk_id from chunk_text limit 1").fetchone()[0]
c.execute("update chunk_text set text = text || ' __extra__' where chunk_id=?", (row,))
c.commit(); c.close()
h2 = logical_database_sha256(TMP)
rec("正文变更会改变语料哈希", h2 != h1, "%s vs %s" % (h1[:12], h2[:12]))
for p in (TMP,TMP+"-wal",TMP+"-shm"):
    try: os.remove(p)
    except Exception: pass

# 短查询：词中截断是否改善
store = Store(s.db_path); tr = QuoteTracer(store, s)
con = sqlite3.connect(str(s.db_path))
raw = con.execute("""select t.text from chunks c join chunk_text t on t.chunk_id=c.id
   join documents d on d.id=c.doc_id where d.has_fulltext=1 and length(t.text)>1200 limit 1""").fetchone()[0]
con.close()
def bench(q, n=3):
    ts=[]
    for _ in range(n):
        t0=time.perf_counter(); r,_=tr.trace(q, allow_online=False); ts.append((time.perf_counter()-t0)*1000)
    return statistics.median(ts), r.level
mid, l1 = bench(raw[:50])
word, l2 = bench(raw[:50].rsplit(" ",1)[0])
print("\n硬截 50 字（切在词中）: %.1f ms  level=%s" % (mid, l1))
print("词边界截断（约50字）  : %.1f ms  level=%s" % (word, l2))
rec("词中截断不再百毫秒级", mid < 60, "%.1f ms" % mid)

fails=[x for x in RES if not x["ok"]]
print("\n== 哈希口径与短查询：%d 项，失败 %d ==" % (len(RES), len(fails)))
for f in fails: print("  FAIL:", f["name"], f["detail"][:120])
