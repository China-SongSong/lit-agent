# -*- coding: utf-8 -*-
import sys, time, json
sys.path.insert(0, r"C:\Users\Lenovo\Desktop\lit_agent\作品相关代码\scr")
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.agent.loop import LiteratureAgent
s = get_settings(); store = Store(s.db_path); store.init_schema()
qs = ["你这边的语料库收录了多少篇文献？",
      "帮我找几篇关于 AlphaFold 的论文",
      "判定这句话的出处：A prominent example of these technologies' transformative impact on science is AlphaFold."]
out = []
for i, q in enumerate(qs, 1):
    ag = LiteratureAgent(store, s)
    t0 = time.monotonic()
    try:
        r = ag.run(q)
        dt = time.monotonic()-t0
        out.append(dict(q=q[:40], rounds=r.rounds, sec=round(dt,2),
                        tools=[st.tool for st in r.steps if st.tool],
                        stop=r.stopped_reason, err=(r.error or "")[:100],
                        ans=(r.answer or "")[:120]))
        print("Q%d rounds=%d sec=%.1f tools=%s stop=%s" % (i, r.rounds, dt, [st.tool for st in r.steps if st.tool], r.stopped_reason), flush=True)
    except Exception as e:
        out.append(dict(q=q[:40], err=str(e)[:150])); print("Q%d EXC %s" % (i, str(e)[:120]), flush=True)
    finally:
        ag.close()
json.dump(out, open(r"C:/Users/Lenovo/Desktop/lit_agent/测试资产/data\agent.json","w",encoding="utf-8"), ensure_ascii=False, indent=1)
print("DONE")
