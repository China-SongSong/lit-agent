# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys, sqlite3
sys.path.insert(0, str(_REPO_ROOT / '作品相关代码' / 'scr'))
from litagent.config import get_settings
s = get_settings(); c = sqlite3.connect(str(s.db_path))
print("=== 各表行数 ===")
for t in ("documents","chunks","chunk_text","postings","fingerprints","summaries","llm_cache","ingest_log","meta"):
    try: print("  %-14s %d" % (t, c.execute("select count(*) from %s" % t).fetchone()[0]))
    except Exception as e: print("  %-14s ERR" % t)
print("\n=== summaries.created_at 分布 ===")
for r in c.execute("select substr(created_at,1,10) d, count(*) from summaries group by d"): print("  ", r)
print("\n=== 微改动测试：原文是否含 ' the ' ===")
raw = c.execute("""select t.text from chunks c join chunk_text t on t.chunk_id=c.id
   join documents d on d.id=c.doc_id where d.has_fulltext=1 and length(t.text)>1200 limit 1""").fetchone()[0]
q = " ".join(raw.split())[150:330]
print("  含 ' the ':", " the " in q, " 长度:", len(q))
print("  样例:", q[:100])
c.close()
