# -*- coding: utf-8 -*-
import sys, os
SCR = r"C:\Users\Lenovo\Desktop\lit_agent\作品相关代码\scr"
sys.path.insert(0, SCR); os.chdir(SCR)
from streamlit.testing.v1 import AppTest
at = AppTest.from_file(os.path.join(SCR, "app.py"), default_timeout=120)
at.run()
quote = open(r"C:/Users/Lenovo/Desktop/lit_agent/测试资产/data\quote.txt", encoding="utf-8").read().strip()
print("text_area 数量:", len(at.text_area))
at.text_area[0].set_value(quote).run()
print("按钮:", [str(b.label) for b in at.button])
idx = [i for i, b in enumerate(at.button) if "开始溯源" in str(b.label)][0]
at.button[idx].click().run()
print("异常:", [e.value for e in at.exception])
for kind in ["success","warning","error","info","metric","subheader","caption"]:
    els = getattr(at, kind, [])
    vals = []
    for e in els:
        v = getattr(e, "value", None)
        if kind == "metric":
            v = "%s=%s" % (getattr(e,"label",""), v)
        vals.append(str(v)[:70])
    print("%-9s %d: %s" % (kind, len(vals), vals[:5]))
