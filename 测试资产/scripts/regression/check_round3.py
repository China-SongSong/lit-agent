# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys, sqlite3
sys.path.insert(0, str(_REPO_ROOT / '作品相关代码' / 'scr'))
from litagent.config import get_settings
s = get_settings()
c = sqlite3.connect(str(s.db_path))
print("文献=%d 片段=%d 倒排=%d 指纹=%d 总结=%d" % (
  c.execute("select count(*) from documents").fetchone()[0],
  c.execute("select count(*) from chunks").fetchone()[0],
  c.execute("select count(*) from postings").fetchone()[0],
  c.execute("select count(*) from fingerprints").fetchone()[0],
  c.execute("select count(*) from summaries").fetchone()[0]))
print("完整性:", c.execute("pragma integrity_check").fetchone()[0])
print("\n总结字段完整度:")
rows = c.execute("select doc_id, tldr, problem, methods, findings, contributions, limitations from summaries").fetchall()
full = part = empty = 0
for r in rows:
    n = sum(1 for x in r[1:] if x and str(x).strip())
    if n == 6: full += 1
    elif n == 0: empty += 1
    else: part += 1
    print("  %-46s %d/6 %s" % (r[0][:44], n, "" if n==6 else "缺%d" % (6-n)))
print("完整 %d / 部分 %d / 全空 %d" % (full, part, empty))
c.close()
