# -*- coding: utf-8 -*-
import sqlite3
db = r"C:\Users\Lenovo\Desktop\lit_agent\作品相关代码\scr\data\index.db"
c = sqlite3.connect(db)
n = c.execute("select count(*) from ingest_log where updated_at like '2026-10-29T14:09:56%'").fetchone()[0]
print("待删除 ingest_log 行:", n)
c.execute("delete from ingest_log where updated_at like '2026-10-29T14:09:56%'")
c.commit()
print("删除后总数:", c.execute("select count(*) from ingest_log").fetchone()[0])
print("integrity:", c.execute("pragma integrity_check").fetchone()[0])
c.close()
