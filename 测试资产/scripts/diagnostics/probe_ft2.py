# -*- coding: utf-8 -*-
import sys, sqlite3
sys.path.insert(0, r"C:\Users\Lenovo\Desktop\lit_agent\作品相关代码\scr")
from litagent.config import get_settings
s = get_settings()
con = sqlite3.connect(str(s.db_path)); cur = con.cursor()
print("has_fulltext 与正文长度对照：")
rows = cur.execute("""select d.has_fulltext, length(f.text) as L
                      from documents d join documents_fulltext f on f.doc_id=d.id
                      order by d.has_fulltext desc, L desc""").fetchall()
from collections import defaultdict
g = defaultdict(list)
for h, L in rows: g[h].append(L)
for h in sorted(g, reverse=True):
    Ls = sorted(g[h], reverse=True)
    print("  has_fulltext=%d  n=%d  长度 min=%d max=%d 中位=%d" % (h, len(Ls), min(Ls), max(Ls), Ls[len(Ls)//2]))
print("\n是否存在 has_fulltext=1 但正文过短（<2000 字符）的误判：")
bad = cur.execute("""select d.id, length(f.text) from documents d join documents_fulltext f on f.doc_id=d.id
                     where d.has_fulltext=1 and length(f.text) < 2000""").fetchall()
print("  ", bad if bad else "无")
print("\n是否存在 has_fulltext=0 但正文很长（>5000 字符）的漏判：")
miss = cur.execute("""select d.id, length(f.text) from documents d join documents_fulltext f on f.doc_id=d.id
                      where d.has_fulltext=0 and length(f.text) > 5000""").fetchall()
print("  ", miss if miss else "无")
