# -*- coding: utf-8 -*-
import sys, sqlite3, os, glob, datetime
sys.path.insert(0, r"C:\Users\Lenovo\Desktop\lit_agent\作品相关代码\scr")
from litagent.config import get_settings
from litagent.index.store import Store
s = get_settings(); store = Store(s.db_path); store.init_schema()
con = sqlite3.connect(str(s.db_path))
ids = [r[0] for r in con.execute("select id from documents where fetched_at like '2026-10-29T14:09:56%'").fetchall()]
print("待回滚文献:", len(ids))
con.close()
for did in ids:
    try:
        store.delete_document(did)
        print("  已删除", did)
    except Exception as e:
        print("  删除失败", did, e)
store.close()
c = sqlite3.connect(str(s.db_path))
print("\n回滚后 文献=%d 片段=%d fingerprint=%d" % (
    c.execute("select count(*) from documents").fetchone()[0],
    c.execute("select count(*) from chunks").fetchone()[0],
    c.execute("select count(*) from fingerprints").fetchone()[0]))
orph = {
 "chunk->doc": c.execute("select count(*) from chunks k left join documents d on d.id=k.doc_id where d.id is null").fetchone()[0],
 "postings->chunk": c.execute("select count(*) from postings p left join chunks k on k.id=p.chunk_id where k.id is null").fetchone()[0],
 "fp->chunk": c.execute("select count(*) from fingerprints f left join chunks k on k.id=f.chunk_id where k.id is null").fetchone()[0],
 "chunk_text->chunk": c.execute("select count(*) from chunk_text t left join chunks k on k.id=t.chunk_id where k.id is null").fetchone()[0],
}
print("孤儿:", orph)
print("integrity:", c.execute("pragma integrity_check").fetchone()[0])
c.close()

print("\n今日新增的数据文件:")
base = r"C:\Users\Lenovo\Desktop\lit_agent\作品相关代码\scr\data"
today = datetime.date(2026,10,29)
for sub in ("raw","pdfs","clean","summaries"):
    p = os.path.join(base, sub)
    hits = []
    for root, dirs, files in os.walk(p):
        for f in files:
            fp = os.path.join(root, f)
            if datetime.date.fromtimestamp(os.path.getmtime(fp)) == today:
                hits.append(os.path.relpath(fp, base))
    print("  %-10s %d 个: %s" % (sub, len(hits), hits[:6]))
