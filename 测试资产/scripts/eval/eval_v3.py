# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys, tempfile, json
sys.path.insert(0, str(_REPO_ROOT / '作品相关代码' / 'scr'))
from litagent.evaluation.calibration import load_corpus, load_cases, build_evaluation_store
from litagent.retrieve.trace import QuoteTracer

FX = Path(str(_REPO_ROOT / '作品相关代码' / 'scr' / 'tests' / 'fixtures'))
work = Path(tempfile.mkdtemp(prefix="ev_"))
store, settings = build_evaluation_store(FX/"calibration_corpus_v2.jsonl", work)
tr = QuoteTracer(store, settings)
cases = load_cases(FX/"calibration_queries_v3.jsonl")
print("评测库文档 %d，查询 %d" % (store.count_documents(), len(cases)))

TH = 0.72
tp=fp=fn=tn=0; rows=[]
for c in cases:
    r,_ = tr.trace(c.query, allow_online=False, found_threshold=TH)
    got = r.hit.doc_id if (r.found and r.hit) else None
    if c.should_find:
        ok = bool(r.found) and (not c.expected_doc_ids or got in c.expected_doc_ids)
        tp += ok; fn += (not ok)
    else:
        ok = (not r.found) or (got in (c.expected_doc_ids or []))
        tn += ok; fp += (not ok)
    rows.append((c.id, c.query_type, c.split, c.should_find, r.found, got, c.expected_doc_ids,
                 round(r.confidence,4), r.level, ok))

print("\n阈值 %.2f：TP=%d FP=%d FN=%d TN=%d 精确率=%.3f 召回率=%.3f" % (
    TH, tp, fp, fn, tn, tp/(tp+fp) if tp+fp else 0, tp/(tp+fn) if tp+fn else 0))

print("\n=== 误报（不应找到却找到）===")
for x in rows:
    if not x[3] and not x[9]:
        print("  %-30s 类型=%-14s 期望=无 实际=%s conf=%.4f level=%s" % (x[0], x[1], x[5], x[7], x[8]))
print("\n=== 漏报（应找到却未找到）===")
for x in rows:
    if x[3] and not x[9]:
        print("  %-30s 类型=%-14s 期望=%s 实际=%s conf=%.4f level=%s" % (x[0], x[1], x[6], (x[5] or "无"), x[7], x[8]))

print("\n=== 按类型统计 ===")
from collections import defaultdict
agg = defaultdict(lambda: [0,0])
for x in rows:
    agg[x[1]][0] += 1; agg[x[1]][1] += x[9]
for k,(n,ok) in sorted(agg.items(), key=lambda kv:-kv[1][0]):
    print("  %-14s %2d 条，通过 %2d（%.0f%%）" % (k, n, ok, 100*ok/n))
