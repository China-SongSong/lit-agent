# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys, os, sqlite3, json, time, traceback
sys.path.insert(0, str(_REPO_ROOT / '作品相关代码' / 'scr'))
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.retrieve.trace import QuoteTracer
from litagent.retrieve.search import HybridSearcher

RES = []
def rec(cat, name, ok, detail=""):
    RES.append(dict(cat=cat, name=name, ok=bool(ok), detail=str(detail)[:300]))
    print("%-6s [%s] %-40s %s" % ("PASS" if ok else "FAIL", cat, name, str(detail)[:110]))

s = get_settings()
tmp = os.path.join(str(_REPO_ROOT / '测试资产' / 'data'), "del_copy.db")
for p in (tmp, tmp+"-wal", tmp+"-shm"):
    try: os.remove(p)
    except Exception: pass
src = sqlite3.connect(str(s.db_path)); dst = sqlite3.connect(tmp); src.backup(dst); dst.close(); src.close()
store = Store(tmp); store.init_schema()
tracer = QuoteTracer(store, s); searcher = HybridSearcher(store, s)
con = sqlite3.connect(tmp); cur = con.cursor()

# 选一篇有全文的文献，取逐字片段
row = cur.execute("""select c.doc_id, t.text from chunks c join chunk_text t on t.chunk_id=c.id
                     join documents d on d.id=c.doc_id where d.has_fulltext=1 and length(t.text)>1200 limit 1""").fetchone()
victim, raw = row[0], " ".join(row[1].split())
q = raw[150:330]
r_before, _ = tracer.trace(q, allow_online=False)
rec("删除一致性", "删除前可溯源到该文献", r_before.found and r_before.hit and r_before.hit.doc_id == victim,
    "doc=%s conf=%.3f" % (victim[:40], r_before.confidence))
title = cur.execute("select title from documents where id=?", (victim,)).fetchone()[0]
hits_before = [h for h in searcher.search(title[:40], top_k=10)[0 if isinstance(searcher.search(title[:40], top_k=10), tuple) else 0] if True] if False else None
before_cnt = cur.execute("select count(*) from documents").fetchone()[0]

store.delete_document(victim)
after_cnt = cur.execute("select count(*) from documents").fetchone()[0]
rec("删除一致性", "删除后文献数减一", after_cnt == before_cnt - 1, "%d→%d" % (before_cnt, after_cnt))

r_after, _ = tracer.trace(q, allow_online=False)
rec("删除一致性", "删除后溯源不再返回该文献", not (r_after.hit and r_after.hit.doc_id == victim),
    "level=%s conf=%.3f" % (r_after.level, r_after.confidence))

left = {
  "chunks": cur.execute("select count(*) from chunks where doc_id=?", (victim,)).fetchone()[0],
  "postings": cur.execute("select count(*) from postings p left join chunks k on k.id=p.chunk_id where k.id is null").fetchone()[0],
  "fingerprints": cur.execute("select count(*) from fingerprints f left join chunks k on k.id=f.chunk_id where k.id is null").fetchone()[0],
  "fulltext": cur.execute("select count(*) from documents_fulltext where doc_id=?", (victim,)).fetchone()[0],
}
rec("删除一致性", "删除后无残留", all(v == 0 for v in left.values()), left)
rec("删除一致性", "删除后数据库完整", cur.execute("pragma integrity_check").fetchone()[0] == "ok")

# 索引重建一致性
from litagent.index.builder import IndexBuilder
t0 = time.time()
rep = IndexBuilder(store, s).build_lexical(rebuild=True)
dt = time.time() - t0
no_post = cur.execute("select count(*) from chunks c where not exists(select 1 from postings p where p.chunk_id=c.id)").fetchone()[0]
no_fp = cur.execute("select count(*) from chunks c where not exists(select 1 from fingerprints f where f.chunk_id=c.id)").fetchone()[0]
n_chunk = cur.execute("select count(*) from chunks").fetchone()[0]
rec("索引重建", "重建后每片段都有倒排", no_post == 0, "缺失 %d / %d" % (no_post, n_chunk))
rec("索引重建", "重建后每片段都有指纹", no_fp == 0, "缺失 %d / %d" % (no_fp, n_chunk))
rec("索引重建", "重建耗时合理", dt < 120, "%.1f 秒（%d 片段）" % (dt, n_chunk))
orph = cur.execute("select count(*) from postings p left join chunks k on k.id=p.chunk_id where k.id is null").fetchone()[0] + \
       cur.execute("select count(*) from fingerprints f left join chunks k on k.id=f.chunk_id where k.id is null").fetchone()[0]
rec("索引重建", "重建后无孤儿索引", orph == 0, orph)

# 重建后溯源仍可用
r2, _ = tracer.trace(q, allow_online=False)
rec("索引重建", "重建后溯源功能正常", isinstance(r2.level, str) and r2.level != "", "level=%s conf=%.3f" % (r2.level, r2.confidence))

# 总结导出
try:
    from litagent.summarize.runner import SummaryRunner
    out = os.path.join(str(_REPO_ROOT / '测试资产' / 'data'), "export_test.jsonl")
    path = SummaryRunner(store, s).export_summaries(path=out)
    with open(path, encoding="utf-8") as f:
        n = sum(1 for _ in f)
    n_sum = cur.execute("select count(*) from summaries").fetchone()[0]
    rec("导出", "总结导出条数等于库内总结数", n == n_sum, "导出 %d / 库内 %d" % (n, n_sum))
    os.remove(path)
except Exception:
    rec("导出", "总结导出", False, traceback.format_exc()[-140:])

con.close(); store.close()
for p in (tmp, tmp+"-wal", tmp+"-shm"):
    try: os.remove(p)
    except Exception: pass

fails = [r for r in RES if not r["ok"]]
print("\n== 第二轮B：%d 项，失败 %d ==" % (len(RES), len(fails)))
for f in fails: print("  FAIL:", f["cat"], f["name"], f["detail"][:130])
