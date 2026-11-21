# -*- coding: utf-8 -*-
import sys, sqlite3
sys.path.insert(0, r"C:\Users\Lenovo\Desktop\lit_agent\作品相关代码\scr")
from litagent.config import get_settings
s = get_settings(); c = sqlite3.connect(str(s.db_path))
rows = c.execute("select doc_id,tldr,problem,methods,findings,contributions,limitations from summaries").fetchall()
full=part=empty=0
for r in rows:
    n = sum(1 for x in r[1:] if x and str(x).strip())
    if n==6: full+=1
    elif n==0: empty+=1
    else: part+=1
    print("  %-46s %d/6" % (r[0][:44], n))
print("完整 %d / 部分 %d / 全空 %d / 共 %d" % (full, part, empty, len(rows)))
print("完整性:", c.execute("pragma integrity_check").fetchone()[0])
c.close()
