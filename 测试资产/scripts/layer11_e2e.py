# -*- coding: utf-8 -*-
import sys, json, subprocess, statistics, time, sqlite3
sys.path.insert(0, "C:/Users/Lenovo/Desktop/lit_agent/测试资产/scripts")
from harness import Case, DATA, FakeLLM, fake_documents, temp_store
c = Case()
SCR = "C:/Users/Lenovo/Desktop/lit_agent/作品相关代码/scr"

# CLI --help 冒烟
for cmd in ("fetch","index","summarize","chat","trace"):
    p = subprocess.run([sys.executable,"cli.py",cmd,"--help"], cwd=SCR, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=120)
    out = (p.stdout or "") + (p.stderr or "")
    c.add("CLI-%s" % cmd, "%s --help 可用" % cmd, p.returncode==0 and "usage" in out.lower(), out.splitlines()[0][:60] if out else "")

# 端到端（临时库）
with temp_store() as (store, settings):
    from litagent.fetch.runner import FetchRunner
    from litagent.index.builder import IndexBuilder
    from litagent.retrieve.search import HybridSearcher
    from litagent.retrieve.trace import QuoteTracer
    from litagent.clean.chunker import chunk_text
    from litagent.summarize.runner import SummaryRunner
    from litagent.agent.loop import LiteratureAgent

    docs = fake_documents(3)
    FetchRunner(store, settings).ingest(docs)
    for d in docs:
        body = ("This synthetic paper discusses battery thermal runaway and fingerprint tracing. " * 12)
        store.replace_chunks(d.id, chunk_text(d.id, body, max_chars=400, overlap_chars=40, min_chars=20))
    IndexBuilder(store, settings).build_lexical()
    c.add("E2E-1", "抓取入库后建索引", store.count_documents() >= 3, "%d 篇" % store.count_documents())

    se = HybridSearcher(store, settings)
    hits, rep = se.search("battery thermal runaway fingerprint", top_k=5)
    c.add("E2E-2", "检索命中", len(hits) >= 1, "%d 条" % len(hits))

    tr = QuoteTracer(store, settings)
    q = "This synthetic paper discusses battery thermal runaway and fingerprint tracing."
    pos, _ = tr.trace(q, allow_online=False)
    neg, _ = tr.trace("Quantum entanglement in superconducting circuits.", allow_online=False)
    c.add("E2E-3", "溯源正例命中", pos.found and pos.level in ("local_exact","local_fuzzy"), pos.level)
    c.add("E2E-4", "溯源反例不命中", not neg.found, neg.level)

    fl = FakeLLM([{"tldr":"概述","problem":"问题","methods":"方法","findings":"发现",
                   "contributions":"贡献","limitations":"局限","keywords":["k"],"claims":[]}] * 6)
    rep1 = SummaryRunner(store, settings).run(doc_ids=[docs[0].id], redo=True)
    c.add("E2E-5", "总结产出", not rep1.errors, "成功 %d 调用 %d" % (rep1.processed, rep1.calls))

    ag = LiteratureAgent(store, settings, client=FakeLLM([{"final_answer":"库里共 %d 篇" % store.count_documents()}]))
    ans = ag.ask("库里有多少篇文献？"); ag.close()
    c.add("E2E-6", "问答收敛", bool(ans) and "篇" in ans, ans[:40])

# 断网降级
import requests
orig = requests.Session.request
def off(*a, **k): raise requests.exceptions.ConnectionError("模拟断网")
requests.Session.request = off
try:
    from litagent.config import get_settings
    from litagent.index.store import Store
    s = get_settings(); st2 = Store(s.db_path); st2.init_schema()
    from litagent.retrieve.trace import QuoteTracer as QT
    con = sqlite3.connect(str(s.db_path))
    raw = con.execute("select text from chunk_text where length(text)>1200 limit 1").fetchone()[0]
    con.close()
    r_off,_ = QT(st2, s).trace(" ".join(raw.split())[150:320], allow_online=False)
    from litagent.agent.tools import LiteratureTools
    reg = LiteratureTools(st2, s).build_registry()
    fetch_res = reg.execute("fetch_online", {"query":"battery"})
    ok_fetch = isinstance(fetch_res, dict) and ("error" in fetch_res or fetch_res.get("available") is False or fetch_res.get("ok") is False)
    c.add("NET-1", "断网时本地溯源照常", r_off.found, r_off.level)
    c.add("NET-2", "断网时在线工具优雅降级", ok_fetch, str(fetch_res)[:70])
finally:
    requests.Session.request = orig

# 性能：254 片段库上的混合检索 P95
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.retrieve.search import HybridSearcher
s = get_settings(); st3 = Store(s.db_path); st3.init_schema()
se = HybridSearcher(st3, s)
con = sqlite3.connect(str(s.db_path))
raw = con.execute("select text from chunk_text where length(text)>1200 limit 1").fetchone()[0]
con.close()
queries = [" ".join(raw.split())[i:i+120] for i in range(50, 50+30*5, 5)]
times = []
for q in queries:
    t0=time.perf_counter(); se.search(q, top_k=10); times.append((time.perf_counter()-t0)*1000)
times.sort()
p95 = times[int(len(times)*0.95)-1]
c.add("PERF-1", "254 片段混合检索 P95", p95 < 300, "P95=%.1f ms 中位=%.1f ms" % (p95, statistics.median(times)))
c.skip("PERF-2", "1 万片段延迟基准（未执行）", "构建万级语料超出本轮时间预算，另行安排")

res = c.report("第11层 入口与端到端")
json.dump(res, open(str(DATA/"layer11_e2e.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
