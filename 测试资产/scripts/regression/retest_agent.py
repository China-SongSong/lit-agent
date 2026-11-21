# -*- coding: utf-8 -*-
import sys, time, json, sqlite3
sys.path.insert(0, r"C:\Users\Lenovo\Desktop\lit_agent\作品相关代码\scr")
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.agent.loop import LiteratureAgent
s = get_settings(); store = Store(s.db_path); store.init_schema()
def counts():
    c = sqlite3.connect(str(s.db_path))
    r = (c.execute("select count(*) from documents").fetchone()[0],
         c.execute("select count(*) from chunks").fetchone()[0])
    c.close(); return r
print("max_rounds =", s.agent_max_rounds)
before = counts(); print("测试前: 文献=%d 片段=%d" % before)
qs = ["你这边的语料库收录了多少篇文献？",
      "帮我找几篇关于 AlphaFold 的论文",
      "判定这句话的出处：A prominent example of these technologies' transformative impact on science is AlphaFold."]
out = []
for i, q in enumerate(qs, 1):
    ag = LiteratureAgent(store, s); t0 = time.monotonic()
    try:
        r = ag.run(q); dt = time.monotonic()-t0
        tools_used = [st.tool for st in r.steps if st.tool]
        out.append(dict(i=i, q=q[:36], rounds=r.rounds, sec=round(dt,2), tools=tools_used,
                        stop=r.stopped_reason, ans=(r.answer or "")[:200]))
        print("Q%d rounds=%d sec=%.1f stop=%s tools=%s" % (i, r.rounds, dt, r.stopped_reason, tools_used), flush=True)
    except Exception as e:
        out.append(dict(i=i, err=str(e)[:150])); print("Q%d EXC %s" % (i, str(e)[:100]), flush=True)
    finally:
        ag.close()
after = counts()
print("\n测试后: 文献=%d 片段=%d" % after)
print("数据库是否被写入:", "否（未变）" if after == before else "是（%+d 篇）" % (after[0]-before[0]))
json.dump(dict(before=before, after=after, runs=out), open(r"C:/Users/Lenovo/Desktop/lit_agent/测试资产/data\retest_agent.json","w",encoding="utf-8"), ensure_ascii=False, indent=1)
for o in out:
    print("\n--- Q%s ---" % o.get("i"))
    print((o.get("ans") or o.get("err") or "")[:260])
