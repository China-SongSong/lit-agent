# -*- coding: utf-8 -*-
import sys, sqlite3
sys.path.insert(0, r"C:\Users\Lenovo\Desktop\lit_agent\作品相关代码\scr")
from litagent.config import get_settings
s = get_settings()
con = sqlite3.connect(str(s.db_path)); cur = con.cursor()
print("文献总数:", cur.execute("select count(*) from documents").fetchone()[0])
print("片段总数:", cur.execute("select count(*) from chunks").fetchone()[0])
print("\n按入库时间倒序前 12 条：")
for row in cur.execute("select id, substr(title,1,48), fetched_at, source from documents order by fetched_at desc limit 12"):
    print("  %-46s %-28s %s" % (row[1], row[2], row[3]))
print("\n今天(2026-10-29)入库的文献:")
rows = cur.execute("select id, substr(title,1,50), fetched_at from documents where fetched_at like '2026-10-29%' order by fetched_at").fetchall()
for r in rows: print("  ", r)
print("  合计:", len(rows))
