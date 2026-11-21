# -*- coding: utf-8 -*-
import sys, json, inspect
sys.path.insert(0, "C:/Users/Lenovo/Desktop/lit_agent/测试资产/scripts")
from harness import Case, DATA, FakeLLM, temp_store
c = Case()
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.agent.loop import LiteratureAgent
from litagent.agent.tools import LiteratureTools
from litagent.agent import prompts as P
from litagent.llm.client import LLMError

s = get_settings(); store = Store(s.db_path); store.init_schema()
CALL = {"action":{"tool":"corpus_stats","args":{}}}

def build(script, **kw):
    st = FakeLLM(script)
    return st, LiteratureAgent(store, s, client=st, **kw)

# 55 LLMError → llm_error
st, ag = build([LLMError("上游 500")])
r = ag.run("q"); ag.close()
c.add("55.1", "LLMError 归为 llm_error", r.stopped_reason in ("llm_error","error"), r.stopped_reason)

# history 透传
st, ag = build([{"final_answer":"ok"}])
r = ag.run("当前问题", history=[{"role":"user","content":"上一轮问题"},{"role":"assistant","content":"上一轮回答"}])
sent = json.dumps(st.messages[0], ensure_ascii=False) if st.messages else ""
ag.close()
c.add("55.2", "history 透传进消息", "上一轮问题" in sent and "当前问题" in sent,
      "含历史=%s" % ("上一轮问题" in sent))

# observation 截断
small = s.__class__(**{**s.__dict__, "agent_observation_chars": 600})
long_call = {"action":{"tool":"search_papers","args":{"query":"lithium battery thermal runaway","limit":5}}}
st = FakeLLM([long_call, {"final_answer":"done"}])
ag2 = LiteratureAgent(store, small, client=st)
r2 = ag2.run("q"); ag2.close()
obs = r2.steps[0].observation or ""
c.add("55.3", "observation 超长截断", len(obs) <= 620, "限制 %d，实际 %d 字" % (small.agent_observation_chars, len(obs)))

# on_step 次数
seen = []
st = FakeLLM([CALL, CALL, {"final_answer":"done"}])
ag3 = LiteratureAgent(store, s, client=st)
r3 = ag3.run("q", on_step=lambda step: seen.append(step)); ag3.close()
c.add("55.4", "on_step 回调次数等于步数", len(seen)==len(r3.steps), "%d / %d" % (len(seen), len(r3.steps)))

# 56 工具行为
tools = LiteratureTools(store, s)
reg = tools.build_registry()
names = sorted(reg.names()) if hasattr(reg,"names") else []
print("   工具清单:", names)
res = reg.execute("search_papers", {"query":"lithium battery thermal runaway","limit":3})
c.add("56.1", "search_papers 结构", isinstance(res, dict) and ("results" in res or "error" not in res),
      list(res)[:5] if isinstance(res,dict) else type(res))
res2 = reg.execute("trace_quote", {"quote": " ".join(__import__("sqlite3").connect(str(s.db_path)).execute(
    "select text from chunk_text where length(text)>1200 limit 1").fetchone()[0].split())[150:300]})
c.add("56.2", "trace_quote 走溯源", isinstance(res2, dict) and ("level" in res2 or "found" in res2), list(res2)[:6])
res3 = reg.execute("get_paper_detail", {"doc_id":"不存在的编号"})
c.add("56.3", "get_paper_detail 查无此文返回错误对象",
      isinstance(res3, dict) and ("error" in res3), res3.get("error","")[:50] if isinstance(res3,dict) else None)
res4 = reg.execute("get_summary", {"doc_id":"不存在的编号"})
c.add("56.4", "get_summary 查无总结返回可用性对象", isinstance(res4, dict) and res4.get("available") is False,
      "available=%s status=%s" % (res4.get("available"), res4.get("summary_status")) if isinstance(res4,dict) else None)

# 57 未知工具 / 字符串参数
res5 = reg.execute("no_such_tool", {})
c.add("57.1", "未知工具返回 error 不抛异常", isinstance(res5, dict) and "error" in res5, res5.get("error","")[:40])
res6 = reg.execute("search_papers", '{"query":"battery","limit":2}')
c.add("57.2", "字符串参数自动 JSON 解析", isinstance(res6, dict) and "error" not in res6, list(res6)[:4])

# prompts
sys_prompt = P.build_system_prompt(reg, max_rounds=7)
c.add("58.1", "系统提示含工具名与轮数", all(n in sys_prompt for n in names[:3]) and "7" in sys_prompt,
      "工具命中 %d/%d" % (sum(1 for n in names if n in sys_prompt), len(names)))
if hasattr(P, "build_round_message"):
    msg = P.build_round_message(2, 7, "观察结果")
    text = msg.get("content","") if isinstance(msg, dict) else str(msg)
    c.add("58.2", "build_round_message 剩余轮数", "5" in text and "剩余" in text, text[-40:])
else:
    c.skip("58.2", "build_round_message", "函数不存在")

# 10 评测层
from litagent.evaluation.corpus_build import logical_database_sha256
import shutil, sqlite3, os
TMP = str(DATA/"eval_hash.db")
for p in (TMP, TMP+"-wal", TMP+"-shm"):
    try: os.remove(p)
    except Exception: pass
src = sqlite3.connect(str(s.db_path)); dst = sqlite3.connect(TMP); src.backup(dst); dst.close(); src.close()
from pathlib import Path
h0 = logical_database_sha256(Path(TMP))
con = sqlite3.connect(TMP)
con.execute("update summaries set tldr='__x__' where rowid=1"); con.execute("update meta set value='999' where key='schema_version'")
con.commit(); con.close()
h1 = logical_database_sha256(Path(TMP))
con = sqlite3.connect(TMP); con.execute("update documents set title = title || ' (改)' where rowid=1"); con.commit(); con.close()
h2 = logical_database_sha256(Path(TMP))
c.add("59.1", "总结/元数据变更不动哈希", h0==h1, "%s vs %s" % (h0[:10], h1[:10]))
c.add("59.2", "documents 内容变更必变哈希", h2!=h1, "%s vs %s" % (h1[:10], h2[:10]))
for p in (TMP, TMP+"-wal", TMP+"-shm"):
    try: os.remove(p)
    except Exception: pass

# 校准混淆矩阵对拍（用报告行重算）
out = __import__("subprocess").run([sys.executable,"-B","-m","litagent.evaluation.calibration",
        "--corpus","tests/fixtures/calibration_corpus_v2.jsonl","--queries","tests/fixtures/calibration_queries_v3.jsonl",
        "--format","json"], cwd="C:/Users/Lenovo/Desktop/lit_agent/作品相关代码/scr",
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
try:
    data = json.loads(out.stdout)
    rows = data.get("thresholds") or []
    ok_math = True
    for row in rows:
        tp=row["correct_found"]; fp=row["wrong_found"]; fn=row["missed"]
        p = tp/(tp+fp) if tp+fp else 0.0
        rc = tp/(tp+fn) if tp+fn else 0.0
        total = tp+fp+fn+row["correct_abstain"]
        if abs(p-row["precision"])>1e-6 or abs(rc-row["recall"])>1e-6 or total!=row["total"]: ok_math=False
    c.add("59.3", "校准指标与手算一致", ok_math and rows, "%d 个阈值行" % len(rows))
except Exception as e:
    c.add("59.3", "校准指标与手算一致", False, "%s" % str(e)[:60])

# large_benchmark 确定性
try:
    from litagent.evaluation import large_benchmark as LB
    import tempfile, hashlib, shutil
    from pathlib import Path as _P
    def gen(seed):
        d = tempfile.mkdtemp(prefix="bench_")
        out_dir = _P(d) / "bench"
        info = LB.generate_benchmark(out_dir, document_count=15, seed=seed)
        h = hashlib.sha256()
        for f in sorted(_P(d).rglob("*")):
            if f.is_file(): h.update(f.name.encode()); h.update(f.read_bytes())
        if not list(_P(d).rglob("*")):
            h.update(b"empty")
        shutil.rmtree(d, ignore_errors=True)
        return h.hexdigest(), info
    h1, i1 = gen(42); h2, i2 = gen(42); h3, i3 = gen(7)
    c.add("59.4", "同种子生成一致、不同种子不同", h1==h2 and h1!=h3,
          "同种子 %s… 异种子 %s…" % (h1[:8], h3[:8]))
except Exception as e:
    c.add("59.4", "large_benchmark 确定性", False, "%s: %s" % (type(e).__name__, str(e)[:50]))

res = c.report("第9层 Agent + 第10层 evaluation")
json.dump(res, open(str(DATA/"layer9_10.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
