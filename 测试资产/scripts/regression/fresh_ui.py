# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys, os
SCR = str(_REPO_ROOT / '作品相关代码' / 'scr')
sys.path.insert(0, SCR); os.chdir(SCR)
from streamlit.testing.v1 import AppTest
RES=[]
def rec(name, ok, detail=""):
    RES.append(dict(name=name, ok=bool(ok), detail=str(detail)[:200]))
    print("%-6s %-40s %s" % ("PASS" if ok else "FAIL", name, str(detail)[:100]))

at = AppTest.from_file(os.path.join(SCR,"app.py"), default_timeout=180)
at.run()
rec("应用无异常启动", not at.exception, [e.value for e in at.exception][:1])
rec("页签数量", len(at.tabs) >= 3, "tabs=%d" % len(at.tabs))
import sqlite3
from litagent.config import get_settings
s = get_settings(); c = sqlite3.connect(str(s.db_path))
raw = c.execute("""select t.text from chunks ch join chunk_text t on t.chunk_id=ch.id
   join documents d on d.id=ch.doc_id where d.has_fulltext=1 and length(t.text)>1200 limit 1""").fetchone()[0]
c.close()
q = " ".join(raw.split())[150:330]
at.text_area[0].set_value(q).run()
idx = [i for i,b in enumerate(at.button) if "开始溯源" in str(b.label)]
rec("存在开始溯源按钮", bool(idx), [str(b.label) for b in at.button][:5])
if idx:
    at.button[idx[0]].click().run()
    rec("点击溯源无异常", not at.exception, [e.value for e in at.exception][:1])
    succ=[str(x.value) for x in at.success]
    rec("输出找到出处", any("找到出处" in s for s in succ), succ[:1])
    mets=["%s=%s"%(getattr(m,"label",""),getattr(m,"value","")) for m in at.metric]
    rec("展示置信度", any("置信度" in m for m in mets), mets[:3])
fails=[x for x in RES if not x["ok"]]
print("\n== 界面：%d 项，失败 %d ==" % (len(RES), len(fails)))
