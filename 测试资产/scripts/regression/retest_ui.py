# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys, os
SCR = str(_REPO_ROOT / '作品相关代码' / 'scr')
sys.path.insert(0, SCR); os.chdir(SCR)
from streamlit.testing.v1 import AppTest
RES = []
def rec(name, ok, detail=""):
    RES.append(dict(name=name, ok=bool(ok), detail=str(detail)[:300]))
    print("%-6s %-42s %s" % ("PASS" if ok else "FAIL", name, str(detail)[:120]))

at = AppTest.from_file(os.path.join(SCR, "app.py"), default_timeout=180)
at.run()
rec("应用无异常启动", not at.exception, [e.value for e in at.exception][:1])
rec("页签数量", len(at.tabs) >= 3, "tabs=%d" % len(at.tabs))
quote = open(str(_REPO_ROOT / '测试资产' / 'data' / 'quote.txt'), encoding="utf-8").read().strip()
at.text_area[0].set_value(quote).run()
idx = [i for i, b in enumerate(at.button) if "开始溯源" in str(b.label)]
rec("存在开始溯源按钮", bool(idx), [str(b.label) for b in at.button][:6])
if idx:
    at.button[idx[0]].click().run()
    rec("点击溯源无异常", not at.exception, [e.value for e in at.exception][:1])
    succ = [str(x.value) for x in at.success]
    rec("输出找到出处", any("找到出处" in s for s in succ), succ[:1])
    metrics = ["%s=%s" % (getattr(m, "label", ""), getattr(m, "value", "")) for m in at.metric]
    rec("展示置信度指标", any("置信度" in m for m in metrics), metrics[:3])
    caps = [str(x.value) for x in at.caption]
    rec("展示溯源诊断", any("溯源诊断" in c for c in caps), [c for c in caps if "溯源诊断" in c][:1])
fails = [r for r in RES if not r["ok"]]
print("\n== 界面：%d 项，失败 %d ==" % (len(RES), len(fails)))
for f in fails: print("  FAIL:", f["name"], f["detail"][:120])
