# -*- coding: utf-8 -*-
import sys, time, json, tempfile, shutil, statistics, sqlite3
from pathlib import Path
sys.path.insert(0, "C:/Users/Lenovo/Desktop/lit_agent/测试资产/scripts")
from harness import DATA
from litagent.evaluation.large_benchmark import generate_benchmark
from litagent.evaluation.calibration import build_evaluation_store
from litagent.retrieve.search import HybridSearcher

def build_case(n_docs, seed=1):
    root = Path(tempfile.mkdtemp(prefix="perf_"))
    out = root / "corpus"
    t0 = time.perf_counter()
    info = generate_benchmark(out, document_count=n_docs, seed=seed)
    gen_s = time.perf_counter() - t0
    corpus = next(out.glob("*.jsonl"))
    t1 = time.perf_counter()
    store, settings = build_evaluation_store(corpus, root / "db")
    build_s = time.perf_counter() - t1
    con = sqlite3.connect(str(store.db_path)) if hasattr(store,"db_path") else None
    chunks = store.count_chunks()
    con2 = sqlite3.connect(str(settings.db_path)) if con is None else con
    npost = con2.execute("select count(*) from postings").fetchone()[0]
    return store, settings, chunks, npost, gen_s, build_s, root

# 探针：120 篇
store, settings, chunks, npost, gen1, build1, root1 = build_case(120)
per_doc = chunks / 120 if chunks else 15
print("探针: 120 篇 -> %d 片段, %d 倒排, 生成 %.1fs, 构建 %.1fs (%.2f 片段/篇)" % (
    chunks, npost, gen1, build1, per_doc))

target_docs = int(round(10000 / per_doc)) if per_doc else 600
target_docs = max(200, min(target_docs, 900))
shutil.rmtree(root1, ignore_errors=True)
print("目标规模: %d 篇（预计约 1 万片段）" % target_docs)

store2, settings2, chunks2, npost2, gen2, build2, root2 = build_case(target_docs)
print("实测: %d 篇 -> %d 片段, %d 倒排, 生成 %.1fs, 构建 %.1fs" % (
    chunks2, chunks2, npost2, gen2, build2))

se = HybridSearcher(store2, settings2)
con = sqlite3.connect(str(settings2.db_path))
sample = [r[0] for r in con.execute("select text from chunk_text limit 40")]
con.close()
queries = [" ".join(t.split())[:90] for t in sample if t and len(t.split()) >= 12][:30]
times = []
for q in queries:
    t0 = time.perf_counter(); se.search(q, top_k=10); times.append((time.perf_counter()-t0)*1000)
times.sort()
p50 = statistics.median(times); p95 = times[int(len(times)*0.95)-1]
print("检索: %d 次查询, P50=%.1f ms, P95=%.1f ms, 最大=%.1f ms" % (len(times), p50, p95, times[-1]))

out = {"docs": target_docs, "chunks": chunks2, "postings": npost2,
       "generate_s": round(gen2,1), "build_s": round(build2,1),
       "p50_ms": round(p50,1), "p95_ms": round(p95,1), "max_ms": round(times[-1],1),
       "queries": len(times)}
json.dump(out, open(str(DATA/"perf_10k.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False))
shutil.rmtree(root2, ignore_errors=True)
