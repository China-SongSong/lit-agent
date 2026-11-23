# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys
sys.path.insert(0, str(_REPO_ROOT / '作品相关代码' / 'scr'))
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.agent.loop import LiteratureAgent
from litagent.llm.client import LLMUsage, LLMError
RES=[]
def rec(no, name, ok, detail=""):
    RES.append(dict(no=no, name=name, ok=bool(ok), detail=str(detail)[:300]))
    print("%-6s #%-2s %-40s %s" % ("PASS" if ok else "FAIL", no, name, str(detail)[:100]))

s = get_settings(); store = Store(s.db_path); store.init_schema()

class Stub:
    def __init__(self, script):
        self.script=list(script); self.i=0; self.usage=LLMUsage(); self.calls=0
    def chat_json(self, messages, *, retries=1):
        self.calls+=1
        if self.i >= len(self.script):
            return {"final_answer":"（脚本耗尽）"}
        item=self.script[self.i]; self.i+=1
        if isinstance(item, Exception): raise item
        return item
    def close(self): pass

def build(script):
    st = Stub(script)
    ag = LiteratureAgent(store, s, client=st)
    return ag, st

CALL = {"action":{"tool":"corpus_stats","args":{}}}

# 1 首轮 final_answer
ag, st = build([{"final_answer":"直接回答"}])
r = ag.run("测试问题"); ag.close()
rec(1, "首轮 final_answer 正常收尾", r.stopped_reason=="final_answer" and r.answer=="直接回答" and r.rounds==1,
    "stop=%s rounds=%d answer=%r" % (r.stopped_reason, r.rounds, r.answer[:20]))

# 2 工具调用→回写→再答
ag, st = build([CALL, {"final_answer":"基于统计回答"}])
r = ag.run("测试问题"); ag.close()
ok = (r.stopped_reason=="final_answer" and r.rounds==2 and len(r.steps)>=1
      and r.steps[0].tool=="corpus_stats" and bool(r.steps[0].observation))
rec(2, "工具调用→回写→再答完整轮回", ok,
    "rounds=%d tool=%s obs_len=%d" % (r.rounds, r.steps[0].tool if r.steps else None, len(r.steps[0].observation or "") if r.steps else 0))

# 3 同工具同参数第 3 次被拦截
exec_count = {"n":0}
ag, st = build([CALL, CALL, CALL, {"final_answer":"收尾"}])
orig = ag.registry.execute
def counting(tool, args):
    exec_count["n"] += 1
    return orig(tool, args)
ag.registry.execute = counting
r = ag.run("测试问题"); ag.close()
blocked = any("完全相同的参数" in (x.observation or "") for x in r.steps)
rec(3, "同工具同参数第 3 次被防死循环拦截", blocked and exec_count["n"]==2,
    "执行 %d 次，拦截=%s" % (exec_count["n"], blocked))

# 4 非法 JSON 一次可恢复
ag, st = build([None, {"final_answer":"恢复了"}])
r = ag.run("测试问题"); ag.close()
rec(4, "非法 JSON 一次可恢复", r.stopped_reason=="final_answer" and r.answer=="恢复了" and any(x.error for x in r.steps),
    "stop=%s 错误步=%d" % (r.stopped_reason, sum(1 for x in r.steps if x.error)))

# 5 连续两次非法 → bad_output
ag, st = build([None, None])
r = ag.run("测试问题"); ag.close()
rec(5, "连续两次非法输出按 bad_output 终止", r.stopped_reason=="bad_output" and bool(r.error),
    "stop=%s err=%r" % (r.stopped_reason, (r.error or "")[:40]))

# 6 轮数耗尽强制收尾
ag, st = build([CALL, CALL, CALL, {"final_answer":"强制收尾"}])
r = ag.run("测试问题", max_rounds=3); ag.close()
rec(6, "轮数耗尽触发强制收尾", r.stopped_reason=="max_rounds" and bool(r.answer),
    "stop=%s rounds=%d answer=%r" % (r.stopped_reason, r.rounds, (r.answer or "")[:20]))

# 7 未知工具返回 error 而非抛异常
ag, st = build([{"action":{"tool":"no_such_tool","args":{}}}, {"final_answer":"继续"}])
try:
    r = ag.run("测试问题")
    err_step = [x for x in r.steps if x.tool=="no_such_tool"]
    rec(7, "未知工具返回 error 而非抛异常",
        bool(err_step) and bool(err_step[0].error) and r.stopped_reason=="final_answer",
        "error=%r stop=%s" % ((err_step[0].error or "")[:50] if err_step else None, r.stopped_reason))
except Exception as e:
    rec(7, "未知工具返回 error 而非抛异常", False, "抛出 %s: %s" % (type(e).__name__, str(e)[:60]))
finally:
    ag.close()

fails=[x for x in RES if not x["ok"]]
print("\n== Agent 主循环：%d 项，失败 %d ==" % (len(RES), len(fails)))
for f in fails: print("  FAIL #%s %s %s" % (f["no"], f["name"], f["detail"][:140]))
