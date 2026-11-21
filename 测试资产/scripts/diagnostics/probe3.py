# -*- coding: utf-8 -*-
import sys, sqlite3, os, traceback
sys.path.insert(0, r"C:\Users\Lenovo\Desktop\lit_agent\作品相关代码\scr")
from litagent.config import get_settings
from litagent.index.store import Store
s = get_settings()
con = sqlite3.connect(str(s.db_path)); cur = con.cursor()

print("=== summaries 列 ===")
cols = [r[1] for r in cur.execute("PRAGMA table_info(summaries)").fetchall()]
print(cols)
print("\n=== 前 2 行原始内容 ===")
for row in cur.execute("select * from summaries limit 2"):
    for k, v in zip(cols, row):
        print("  %-16s %r" % (k, (str(v)[:80] if v is not None else None)))
    print("  ---")

print("\n=== Store 连接属性 ===")
st = Store(s.db_path)
attrs = [a for a in dir(st) if "conn" in a.lower() or a in ("db","_db")]
print(attrs)
for a in attrs:
    try: print("  %s -> %r" % (a, type(getattr(st, a))))
    except Exception as e: print("  %s ERR %s" % (a, e))
print("\n=== chunks 列 ===")
print([r[1] for r in cur.execute("PRAGMA table_info(chunks)").fetchall()])
