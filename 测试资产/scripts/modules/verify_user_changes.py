# -*- coding: utf-8 -*-
import sys, json, sqlite3, subprocess
from pathlib import Path
from collections import Counter
sys.path.insert(0, "C:/Users/Lenovo/Desktop/lit_agent/作品相关代码/scr")
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.evaluation.corpus_build import logical_database_sha256, logical_database_table_sha256
from litagent.disciplines import classify_disciplines, TAGGER_VERSION
from litagent.schema import summary_quality
RES=[]
def rec(no, name, ok, detail=""):
    RES.append(dict(no=no, name=name, ok=bool(ok), detail=str(detail)[:300]))
    print("%-6s #%-3s %-40s %s" % ("PASS" if ok else "FAIL", no, name, str(detail)[:95]))

s = get_settings()
SCR = Path("C:/Users/Lenovo/Desktop/lit_agent/作品相关代码/scr")

# 1) 基线 v3
v3 = json.load(open(SCR/"tests/fixtures/production_baseline_v3.json", encoding="utf-8"))
h = logical_database_sha256(s.db_path)
tabs = logical_database_table_sha256(s.db_path)
rec("1", "基线 v3 哈希与当前库一致", h == v3.get("logical_sha256"), "%s vs %s" % (h[:14], str(v3.get("logical_sha256"))[:14]))
rec("2", "v3 覆盖表", set(tabs.keys())=={"documents","documents_fulltext"}, list(tabs.keys()))
rec("3", "v3 记录了排除项", "hash_policy" in v3 and ("exclude" in json.dumps(v3, ensure_ascii=False).lower() or "排除" in json.dumps(v3, ensure_ascii=False)),
    list(v3.keys()))

# 2) 学科分布
con = sqlite3.connect(str(s.db_path))
rows = con.execute("select id,title,abstract from documents").fetchall()
cnt = Counter(); general=[]
for did,title,abstract in rows:
    tags = classify_disciplines(" ".join(x for x in (title or "", abstract or "") if x)) or ["<空>"]
    cnt.update(tags)
    if tags==["综合类"]: general.append((title or "")[:50])
print("\n  标签版本: %s" % TAGGER_VERSION)
print("  综合类: %d / %d" % (len(general), len(rows)))
for t in general[:8]: print("    ", t)
rec("4", "学科综合类占比下降", len(general) <= 2, "%d 篇" % len(general))
bat = classify_disciplines("锂离子电池热失控机理")
cra = classify_disciplines("Focused crawling: a new approach to topic-specific Web resource discovery")
rec("5", "电池主题可归类", any("工程技术" in x for x in bat), bat)
rec("6", "爬虫主题可归类", any("计算机" in x for x in cra), cra)

# 3) summary_quality 新分类
cases = [
 ("题录级短正文", summary_quality(0, list(range(6)), content_chars=127)),
 ("摘要级", summary_quality(0, [0,1], content_chars=800)),
 ("仅论断", summary_quality(0, [0,1,2,3,4,5], content_chars=2000)),
 ("完整", summary_quality(0, [], content_chars=3000)),
]
for name, out in cases: print("    %-12s -> %s" % (name, out))
rec("7", "summary_quality 新分类可用", cases[0][1][0]=="short_content_limited", cases[0][1])

con.close()
fails=[x for x in RES if not x["ok"]]
print("\n== 复核：%d 项，失败 %d ==" % (len(RES), len(fails)))
for f in fails: print("  FAIL #%s %s %s" % (f["no"], f["name"], f["detail"][:140]))
