# -*- coding: utf-8 -*-
import sys, json, subprocess, inspect
sys.path.insert(0, "C:/Users/Lenovo/Desktop/lit_agent/作品相关代码/scr")
out = subprocess.run([sys.executable,"-B","-m","litagent.evaluation.calibration",
    "--corpus","tests/fixtures/calibration_corpus_v2.jsonl","--queries","tests/fixtures/calibration_queries_v3.jsonl",
    "--format","json"], cwd="C:/Users/Lenovo/Desktop/lit_agent/作品相关代码/scr",
    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
data = json.loads(out.stdout)
print("顶层键:", list(data))
rows = data.get("thresholds") or data.get("rows") or []
print("行键:", list(rows[0]) if rows else None)
from litagent.evaluation import large_benchmark as LB
print("\nlarge_benchmark 公开名:", [n for n in dir(LB) if not n.startswith("_")])
for n in ("generate_benchmark","generate_corpus","build_benchmark"):
    if hasattr(LB,n):
        print(n, inspect.signature(getattr(LB,n)))
