# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys, sqlite3, os, shutil, traceback
sys.path.insert(0, str(_REPO_ROOT / '作品相关代码' / 'scr'))
from litagent.config import get_settings
from litagent.index.store import Store
s = get_settings()
con = sqlite3.connect(str(s.db_path)); cur = con.cursor()

print("=== 总结完整度统计 ===")
rows = cur.execute("select doc_id, tldr, problem, methods, findings, contributions, limitations, claims_json from summaries").fetchall()
full = partial = empty = 0
for r in rows:
    fields = [x for x in r[1:7] if x and str(x).strip()]
    if len(fields) == 6: full += 1
    elif len(fields) == 0: empty += 1
    else: partial += 1
print("  完整(6字段全有)=%d  部分=%d  全空(仅claims)=%d  合计=%d" % (full, partial, empty, len(rows)))
for r in rows:
    fields = sum(1 for x in r[1:7] if x and str(x).strip())
    if fields < 6:
        print("    缺字段: %s  非空=%d/6  claims=%s" % (r[0][:42], fields, "有" if r[7] and r[7]!="[]" else "无"))

print("\n=== 删除事务性（副本）===")
tmp = os.path.join(str(_REPO_ROOT / '测试资产' / 'data'), "copy.db")
for p in (tmp, tmp+"-wal", tmp+"-shm"):
    try: os.remove(p)
    except Exception: pass
src = sqlite3.connect(str(s.db_path)); dst = sqlite3.connect(tmp); src.backup(dst); dst.close(); src.close()
st = Store(tmp); st.init_schema()
victim = sqlite3.connect(tmp).execute("select id from documents limit 1").fetchone()[0]
before = st.count_documents()
try:
    st.delete_document(victim)
    st.close()
    c = sqlite3.connect(tmp)
    after = c.execute("select count(*) from documents").fetchone()[0]
    left = {
      "chunks": c.execute("select count(*) from chunks where doc_id=?", (victim,)).fetchone()[0],
      "chunk_text(孤儿)": c.execute("select count(*) from chunk_text t left join chunks k on k.id=t.chunk_id where k.id is null").fetchone()[0],
      "postings(孤儿)": c.execute("select count(*) from postings p left join chunks k on k.id=p.chunk_id where k.id is null").fetchone()[0],
      "fingerprints(孤儿)": c.execute("select count(*) from fingerprints f left join chunks k on k.id=f.chunk_id where k.id is null").fetchone()[0],
      "summaries": c.execute("select count(*) from summaries where doc_id=?", (victim,)).fetchone()[0],
      "fulltext": c.execute("select count(*) from documents_fulltext where doc_id=?", (victim,)).fetchone()[0],
    }
    c.close()
    print("  文献数: %d → %d  %s" % (before, after, "OK" if after == before-1 else "异常"))
    print("  残留:", left, "OK" if all(v==0 for v in left.values()) else "存在残留")
except Exception:
    print("  删除抛异常:")
    traceback.print_exc()
for p in (tmp, tmp+"-wal", tmp+"-shm"):
    try: os.remove(p)
    except Exception: pass
