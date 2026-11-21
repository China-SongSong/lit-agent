# -*- coding: utf-8 -*-
import sys, os, json
from pathlib import Path
sys.path.insert(0, r"C:\Users\Lenovo\Desktop\lit_agent\作品相关代码\scr")
from litagent.config import get_settings
from litagent.evaluation.corpus_build import logical_database_sha256
import litagent.disciplines as D
RES=[]
def rec(name, ok, detail=""):
    RES.append(dict(name=name, ok=bool(ok), detail=str(detail)[:240]))
    print("%-6s %-42s %s" % ("PASS" if ok else "FAIL", name, str(detail)[:100]))

s = get_settings()
SCR = Path(r"C:\Users\Lenovo\Desktop\lit_agent\作品相关代码\scr")
v1 = json.load(open(SCR/"tests/fixtures/production_baseline_v1.json", encoding="utf-8"))
v2 = json.load(open(SCR/"tests/fixtures/production_baseline_v2.json", encoding="utf-8"))
h = logical_database_sha256(s.db_path)
print("基线 v2 字段:", list(v2.keys()))
rec("当前哈希与基线 v2 一致", h == v2.get("logical_sha256"), "%s vs %s" % (h[:16], str(v2.get("logical_sha256"))[:16]))
rec("基线 v2 记录了哈希口径", any(k for k in v2 if "hash" in k or "policy" in k), [k for k in v2 if "hash" in k])
rec("基线 v2 计数与库一致", v2.get("counts",{}).get("documents") == 49 and v2.get("counts",{}).get("chunks") == 254, v2.get("counts"))

# 学科分类
cases = [
 ("图神经网络推荐系统与冷启动", {"计算机科学"}),
 ("锂离子电池热失控机理", {"材料科学","化学工程"}),
]
print("\n学科分类抽查：")
for text, exp in cases:
    try:
        got = D.classify_disciplines(text)
        print("  %-22s -> %s" % (text[:20], got))
    except Exception as e:
        print("  %-22s -> 异常 %s" % (text[:20], e))
try:
    got = D.classify_disciplines("图神经网络推荐系统中的冷启动问题")
    rec("中文计算机类可识别", any("计算机" in str(x) or "computer" in str(x).lower() for x in got), got)
except Exception as e:
    rec("中文计算机类可识别", False, e)
try:
    got = D.classify_disciplines("这段文字没有任何学科特征")
    rec("无特征时返回通用或空", isinstance(got,(list,tuple,set)), got)
except Exception as e:
    rec("无特征时返回通用或空", False, e)

fails=[x for x in RES if not x["ok"]]
print("\n== 基线与学科：%d 项，失败 %d ==" % (len(RES), len(fails)))
for f in fails: print("  FAIL:", f["name"], f["detail"][:120])
