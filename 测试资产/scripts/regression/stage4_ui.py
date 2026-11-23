# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys, os, traceback
SCR = str(_REPO_ROOT / '作品相关代码' / 'scr')
sys.path.insert(0, SCR)
os.chdir(SCR)
from streamlit.testing.v1 import AppTest

RES = []
def rec(name, ok, detail=""):
    RES.append(dict(name=name, ok=bool(ok), detail=str(detail)[:400]))
    print("%-6s %-42s %s" % ("PASS" if ok else "FAIL", name, str(detail)[:140]))

try:
    at = AppTest.from_file(os.path.join(SCR, "app.py"), default_timeout=120)
    at.run()
    rec("应用可无异常启动", not at.exception, [e.value for e in at.exception][:1])
    rec("标题正确", any("lit-agent" in str(t.value) for t in at.title), [t.value for t in at.title])
    rec("页签数量", len(at.tabs) >= 3, "tabs=%d" % len(at.tabs))
    rec("存在文本输入控件", len(at.text_area) >= 1, "textarea=%d" % len(at.text_area))
    rec("存在按钮", len(at.button) >= 1, "button=%d" % len(at.button))
    # 侧边栏指标
    try:
        rec("侧边栏可见", True, "%d 个 markdown" % len(at.sidebar.markdown))
    except Exception as e:
        rec("侧边栏可见", False, e)
    # 触发溯源：填入文本并点击
    quote = open(str(_REPO_ROOT / '测试资产' / 'data' / 'quote.txt'), encoding="utf-8").read().strip()
    if len(at.text_area):
        at.text_area[0].set_value(quote)
        at.run()
        # 找到「开始溯源」按钮
        idx = None
        for i, b in enumerate(at.button):
            if "溯源" in str(b.label):
                idx = i; break
        if idx is not None:
            at.button[idx].click().run()
            rec("点击「开始溯源」无异常", not at.exception, [e.value for e in at.exception][:1])
            body = " ".join(str(x.value) for x in at.markdown) + " " + " ".join(str(x.value) for x in at.success) + " " + " ".join(str(x.value) for x in at.warning) + " " + " ".join(str(x.value) for x in at.error) + " " + " ".join(str(x.value) for x in at.info)
            rec("结果中出现「找到出处」或等级", ("找到出处" in body) or ("local_" in body), body[:120])
            rec("页面显示置信度", ("置信度" in body) or ("confidence" in body), "")
        else:
            rec("找到「开始溯源」按钮", False, [str(b.label) for b in at.button][:8])
except Exception:
    traceback.print_exc()
    rec("应用可无异常启动", False, traceback.format_exc()[-200:])

bad = [r for r in RES if not r["ok"]]
print("\n== 汇总：%d 项，失败 %d ==" % (len(RES), len(bad)))
for b in bad: print("  FAIL:", b["name"], b["detail"][:150])
