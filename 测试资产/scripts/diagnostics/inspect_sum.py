# -*- coding: utf-8 -*-
import sys, sqlite3
sys.path.insert(0, r"C:\Users\Lenovo\Desktop\lit_agent\作品相关代码\scr")
from litagent.config import get_settings
s = get_settings(); c = sqlite3.connect(str(s.db_path))
cols = ["tldr","problem","methods","findings","contributions","limitations"]
for did in ("doi:10.1016/j.joi.2026.101774", "doi:10.1016/j.apenergy.2026.128706"):
    row = c.execute("select %s from summaries where doc_id=?" % ",".join(cols), (did,)).fetchone()
    print("==", did)
    for k, v in zip(cols, row):
        print("   %-14s %r" % (k, (str(v)[:70] if v else v)))
    # 该文献是否有全文
    ft = c.execute("select has_fulltext from documents where id=?", (did,)).fetchone()
    cl = c.execute("select length(text) from documents_fulltext where doc_id=?", (did,)).fetchone()
    print("   has_fulltext=%s 正文长度=%s model=%s" % (ft[0] if ft else "?", cl[0] if cl else "?",
          c.execute("select model from summaries where doc_id=?", (did,)).fetchone()[0]))
c.close()
