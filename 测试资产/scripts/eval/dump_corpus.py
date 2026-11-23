# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import json
p = str(_REPO_ROOT / '作品相关代码' / 'scr' / 'tests' / 'fixtures' / 'calibration_corpus_v2.jsonl')
docs = [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]
print("语料文档数:", len(docs))
for d in docs:
    keys = list(d.keys())
    print("\n--- %s ---" % d.get("id"))
    print("  字段:", keys)
    print("  标题:", (d.get("title") or "")[:80])
    t = d.get("fulltext") or d.get("text") or ""
    print("  正文长度:", len(t), "| 前 160 字:", (t or "")[:160].replace("\n"," "))
