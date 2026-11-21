# -*- coding: utf-8 -*-
import sys, sqlite3
sys.path.insert(0, r"C:\Users\Lenovo\Desktop\lit_agent\作品相关代码\scr")
from litagent.config import get_settings
s = get_settings(); c = sqlite3.connect(str(s.db_path))
print("=== meta ===")
for r in c.execute("select key, substr(value,1,60) from meta"): print("  ", r)
print("\n=== llm_cache 时间分布 ===")
cols = [r[1] for r in c.execute("PRAGMA table_info(llm_cache)").fetchall()]
print("  列:", cols)
if "created_at" in cols:
    for r in c.execute("select substr(created_at,1,10) d, count(*) from llm_cache group by d"): print("  ", r)
print("\n=== ingest_log 时间分布 ===")
for r in c.execute("select substr(updated_at,1,10) d, count(*) from ingest_log group by d"): print("  ", r)
c.close()
