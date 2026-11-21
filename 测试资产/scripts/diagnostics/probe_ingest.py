# -*- coding: utf-8 -*-
import sqlite3
c = sqlite3.connect(r"C:\Users\Lenovo\Desktop\lit_agent\作品相关代码\scr\data\index.db")
cols = [r[1] for r in c.execute("PRAGMA table_info(ingest_log)").fetchall()]
print("列:", cols)
for row in c.execute("select * from ingest_log order by rowid desc limit 4"):
    print("  ", str(row)[:150])
c.close()
