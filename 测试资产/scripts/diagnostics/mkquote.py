# -*- coding: utf-8 -*-
import sys, sqlite3
sys.path.insert(0, r"C:\Users\Lenovo\Desktop\lit_agent\作品相关代码\scr")
from litagent.config import get_settings
s = get_settings()
con = sqlite3.connect(str(s.db_path))
row = con.execute("""select t.text from chunks c join chunk_text t on t.chunk_id=c.id
                     join documents d on d.id=c.doc_id where d.has_fulltext=1
                     and length(t.text) > 1200 limit 1""").fetchone()
q = " ".join(row[0].split())[200:420]
open(r"C:/Users/Lenovo/Desktop/lit_agent/测试资产/data\quote.txt","w",encoding="utf-8").write(q)
print(q)
