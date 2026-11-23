# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys, os, sqlite3, time
sys.path.insert(0, str(_REPO_ROOT / '作品相关代码' / 'scr'))
from litagent.config import get_settings
from litagent.index.store import Store
RES=[]
def rec(no, name, ok, detail=""):
    RES.append(dict(no=no, name=name, ok=bool(ok), detail=str(detail)[:300]))
    print("%-6s #%-2s %-44s %s" % ("PASS" if ok else "FAIL", no, name, str(detail)[:95]))

s = get_settings()
TMP = str(_REPO_ROOT / '测试资产' / 'data' / 'fetch.db')
for p in (TMP, TMP+"-wal", TMP+"-shm"):
    try: os.remove(p)
    except Exception: pass
src = sqlite3.connect(str(s.db_path)); dst = sqlite3.connect(TMP); src.backup(dst); dst.close(); src.close()
store = Store(TMP); store.init_schema()

t0 = time.time()
try:
    from litagent.fetch.openalex import OpenAlexSource
    prov = OpenAlexSource(s)
    docs = prov.search("lithium battery thermal runaway", limit=2)
    rec("24e.1", "在线抓取返回候选", len(docs)>=1, "%d 篇，%.1f 秒" % (len(docs), time.time()-t0))
except Exception as e:
    docs = []
    rec("24e.1", "在线抓取返回候选", False, "%s: %s" % (type(e).__name__, str(e)[:70]))

if docs:
    from litagent.fetch.runner import FetchRunner
    before = store.count_documents()
    rep = FetchRunner(store, s).ingest(docs)
    rec("24e.2", "抓取结果可入库", store.count_documents() >= before,
        "新增 %d，合并 %d" % (store.count_documents()-before, rep.merged))
    from litagent.index.builder import IndexBuilder
    ids = [d.id for d in docs if store.get_document(d.id, with_fulltext=False)]
    if ids:
        IndexBuilder(store, s).build_lexical(doc_ids=ids)
        con = sqlite3.connect(TMP)
        n = con.execute("select count(*) from postings p join chunks c on c.id=p.chunk_id where c.doc_id in (%s)" % ",".join("?"*len(ids)), ids).fetchone()[0]
        con.close()
        rec("24e.3", "新入库文献可建索引", n>0, "倒排 %d 条" % n)

store.close()
for p in (TMP, TMP+"-wal", TMP+"-shm"):
    try: os.remove(p)
    except Exception: pass
fails=[x for x in RES if not x["ok"]]
print("\n== 抓取链路：%d 项，失败 %d ==" % (len(RES), len(fails)))
