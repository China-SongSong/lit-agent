# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys, sqlite3
sys.path.insert(0, str(_REPO_ROOT / '作品相关代码' / 'scr'))
from litagent.config import get_settings
s = get_settings(); c = sqlite3.connect(str(s.db_path))
rows = c.execute("select doc_id, tldr, problem, methods, findings, contributions, limitations from summaries").fetchall()
print("总结行数:", len(rows))
for r in rows:
    fields = r[1:]
    n = sum(1 for x in fields if x and str(x).strip())
    print("  %-46s %d/6 %s" % (r[0][:44], n, "全空" if n==0 else ""))
full = sum(1 for r in rows if all(x and str(x).strip() for x in r[1:]))
print("完整 %d / 共 %d" % (full, len(rows)))
print("文件 mtime:", c.execute("select 1").fetchone() and __import__("os").path.getmtime(str(s.db_path)))
c.close()
