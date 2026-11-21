# -*- coding: utf-8 -*-
import sys, sqlite3, json
sys.path.insert(0, r"C:\Users\Lenovo\Desktop\lit_agent\作品相关代码\scr")
from litagent.config import get_settings
s = get_settings()
con = sqlite3.connect(str(s.db_path)); cur = con.cursor()
cur.execute("PRAGMA table_info(documents_fulltext)")
print("documents_fulltext 列:", [r[1] for r in cur.fetchall()])
cur.execute("select count(*) from documents_fulltext"); print("行数:", cur.fetchone()[0])
cols = [r[1] for r in cur.execute("PRAGMA table_info(documents_fulltext)").fetchall()]
print("样本:")
for row in cur.execute("select %s from documents_fulltext limit 3" % ",".join(cols)):
    d = dict(zip(cols, row))
    for k, v in d.items():
        print("   %-12s %s" % (k, str(v)[:90].replace("\n", " ")))
    print("   ---")
# 与 has_fulltext 对照
print("\nhas_fulltext 分布:")
for row in cur.execute("select has_fulltext, count(*) from documents group by has_fulltext"):
    print("   ", row)
print("\n完整正文长度分布（documents_fulltext.text 非空的长度）:")
try:
    for row in cur.execute("select length(text), count(*) from documents_fulltext group by length(text) order by count(*) desc limit 8"):
        print("   len=%-8s n=%s" % row)
except Exception as e:
    print("   无 text 列或查询失败:", e)
