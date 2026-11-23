# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[2]

import sys, time, json, tempfile, shutil, statistics, sqlite3, random
sys.path.insert(0, str(_REPO_ROOT / '测试资产' / 'scripts'))
from harness import DATA
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.schema import Document, Author
from litagent.clean.chunker import chunk_text
from litagent.index.builder import IndexBuilder
from litagent.retrieve.search import HybridSearcher

s = get_settings()
root = Path(tempfile.mkdtemp(prefix="perf10k_"))
store = Store(root/"index.db"); store.init_schema()

random.seed(7)
vocab = ["battery","thermal","runaway","lithium","fingerprint","citation","provenance","retrieval",
         "calibration","corpus","segment","overlap","precision","recall","threshold","evidence",
         "index","shingle","token","language","model","summary","candidate","verification"]
cjk = ["文献","溯源","指纹","检索","校准","片段","证据","语料"]

def body(n_seg=20, seg_len=420):
    parts=[]
    for _ in range(n_seg):
        words=[random.choice(vocab) for _ in range(seg_len//7)]
        mixed=" ".join(words) + "。" + "".join(random.choice(cjk) for _ in range(6))
        parts.append(mixed)
    return "\n\n".join(parts)

N_DOCS = int(sys.argv[1]) if len(sys.argv) > 1 else 520
OUT_NAME = sys.argv[2] if len(sys.argv) > 2 else "perf_10k.json"
t0=time.perf_counter()
for i in range(N_DOCS):
    d = Document(id="perf:%d"%i, title="Synthetic benchmark paper %d"%i,
                 doi="10.7777/perf.%d"%i, year=2025, venue="Benchmark Journal",
                 authors=[Author(family="Doe", given="J")], language="en", source="perf")
    store.upsert_document(d)
    store.replace_chunks(d.id, chunk_text(d.id, body(), max_chars=500, overlap_chars=50, min_chars=20))
    if (i+1) % 100 == 0: print("  造数 %d/%d 篇" % (i+1, N_DOCS), flush=True)
seed_s = time.perf_counter()-t0
n_chunks = store.count_chunks()
print("造数完成: %d 篇, %d 片段, %.1fs" % (N_DOCS, n_chunks, seed_s), flush=True)

t1=time.perf_counter()
IndexBuilder(store, s).build_lexical()  # 只建词法+指纹，跳过向量，专测检索延迟
build_s = time.perf_counter()-t1
con = sqlite3.connect(str(store.db_path))
npost = con.execute("select count(*) from postings").fetchone()[0]
nfp = con.execute("select count(*) from fingerprints").fetchone()[0]
print("建索引完成: 倒排 %d, 指纹 %d, %.1fs" % (npost, nfp, build_s), flush=True)

se = HybridSearcher(store, s)
rows = [r[0] for r in con.execute("select text from chunk_text limit 40")]
con.close()
queries=[" ".join(t.split())[:90] for t in rows if t][:30]
times=[]
for q in queries:
    t=time.perf_counter(); se.search(q, top_k=10); times.append((time.perf_counter()-t)*1000)
times.sort()
p50=statistics.median(times); p95=times[int(len(times)*0.95)-1]
print("检索: %d 次, P50=%.1f ms, P95=%.1f ms, 最大=%.1f ms, 首条命中=%s" % (
    len(times), p50, p95, times[-1], bool(se.search(queries[0], top_k=5)[0])), flush=True)

out={"docs":N_DOCS,"chunks":n_chunks,"postings":npost,"fingerprints":nfp,
     "seed_s":round(seed_s,1),"build_s":round(build_s,1),
     "p50_ms":round(p50,1),"p95_ms":round(p95,1),"max_ms":round(times[-1],1)}
json.dump(out, open(str(DATA/OUT_NAME),"w",encoding="utf-8"), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False))
store.close(); shutil.rmtree(root, ignore_errors=True)
