# -*- coding: utf-8 -*-
import sys, sqlite3, json
sys.path.insert(0, r"C:\Users\Lenovo\Desktop\lit_agent\作品相关代码\scr")
from litagent.config import get_settings
s = get_settings(); c = sqlite3.connect(str(s.db_path))
for did in ("doi:10.1016/j.apenergy.2026.128706", "doi:10.1016/j.joi.2026.101774"):
    row = c.execute("select tldr, claims_json, keywords_json, created_at from summaries where doc_id=?", (did,)).fetchone()
    claims = json.loads(row[1] or "[]")
    print("==", did)
    print("   tldr=%r" % (row[0] or ""))
    print("   claims 条数=%d  关键词=%s  生成时间=%s" % (len(claims), row[2], row[3]))
    for x in claims[:2]: print("      -", str(x.get("text",""))[:60])
c.close()
