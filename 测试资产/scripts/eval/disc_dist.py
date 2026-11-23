# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys, sqlite3
from collections import Counter
sys.path.insert(0, str(_REPO_ROOT / '作品相关代码' / 'scr'))
from litagent.config import get_settings
from litagent.disciplines import classify_disciplines
s = get_settings(); c = sqlite3.connect(str(s.db_path))
rows = c.execute("select id, title, abstract from documents").fetchall()
cnt = Counter(); general = []
for did, title, abstract in rows:
    text = " ".join(x for x in (title or "", abstract or "") if x)
    tags = classify_disciplines(text) or ["<空>"]
    cnt.update(tags)
    if tags == ["综合类"]:
        general.append((title or "")[:60])
print("全库 %d 篇的学科分布：" % len(rows))
for k, v in cnt.most_common():
    print("  %-22s %d" % (k, v))
print("\n落入综合类的 %d 篇（前 8）：" % len(general))
for t in general[:8]: print("  ", t)
