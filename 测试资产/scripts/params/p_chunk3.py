# -*- coding: utf-8 -*-
import sys, sqlite3
sys.path.insert(0, r"C:\Users\Lenovo\Desktop\lit_agent\作品相关代码\scr")
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.clean.chunker import chunk_text
base = get_settings(); store = Store(base.db_path); store.init_schema()
con = sqlite3.connect(str(base.db_path))
raw = con.execute("select text from chunk_text where length(text)>1500 limit 1").fetchone()[0]
long_text = (raw + "\n\n") * 3

print("=== overlap 扫描（max=800）===")
print("%-8s %-8s %s" % ("重叠","片段数","总字数"))
for o in (0, 50, 200, 400):
    cs = chunk_text("t", long_text, max_chars=800, overlap_chars=o, min_chars=20)
    print("%-8d %-8d %d" % (o, len(cs), sum(len(c.text) for c in cs)))

print("\n=== 极端值 ===")
def probe(name, fn):
    try: print("  %-26s -> %s" % (name, str(fn())[:70]))
    except Exception as e: print("  %-26s -> 异常 %s: %s" % (name, type(e).__name__, str(e)[:60]))
probe("max_chars=0", lambda: "片段数=%d" % len(chunk_text("t", long_text, max_chars=0, overlap_chars=0, min_chars=20)))
probe("max_chars=1", lambda: "片段数=%d" % len(chunk_text("t", long_text, max_chars=1, overlap_chars=0, min_chars=1)))
probe("overlap>max", lambda: "片段数=%d" % len(chunk_text("t", long_text, max_chars=200, overlap_chars=500, min_chars=20)))
probe("min_chars>max_chars", lambda: "片段数=%d" % len(chunk_text("t", long_text, max_chars=200, overlap_chars=50, min_chars=500)))
