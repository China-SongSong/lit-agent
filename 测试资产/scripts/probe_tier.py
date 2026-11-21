# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, "C:/Users/Lenovo/Desktop/lit_agent/作品相关代码/scr")
import litagent.contracts as C
for n in ("openalex","crossref","arxiv","s2","s2","opencli","opencli:cnki","opencli:wanfang","opencli:baidu","local","unknown-x",""):
    print("%-16s -> %s" % (n or "<空>", C.source_tier_for(n)))
print()
import inspect
src = inspect.getsource(C.source_tier_for)
print(src[:1200])
