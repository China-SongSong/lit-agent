# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys, sqlite3
sys.path.insert(0, str(_REPO_ROOT / '作品相关代码' / 'scr'))
from litagent.config import get_settings
s = get_settings()
c = sqlite3.connect(str(s.db_path))
rows = c.execute("select doc_id, tldr, problem, methods, findings, contributions, limitations from summaries").fetchall()
print("总结共 %d 篇" % len(rows))
for r in rows:
    n = sum(1 for x in r[1:] if x and str(x).strip())
    flag = "完整" if n == 6 else ("全空" if n == 0 else "部分")
    if n < 6:
        print("  %-46s %s (%d/6)" % (r[0][:44], flag, n))
print("完整 %d / 部分 %d / 全空 %d" % (
    sum(1 for r in rows if all(x and str(x).strip() for x in r[1:])),
    sum(1 for r in rows if any(x and str(x).strip() for x in r[1:]) and not all(x and str(x).strip() for x in r[1:])),
    sum(1 for r in rows if not any(x and str(x).strip() for x in r[1:]))))
c.close()
