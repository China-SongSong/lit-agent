# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import json, os
p = str(_REPO_ROOT / '作品相关代码' / 'scr' / 'data' / 'evaluation' / 'trace_calibration_v2.json')
d = json.load(open(p, encoding="utf-8"))
print("顶层键:", list(d.keys()))
for k, v in d.items():
    if isinstance(v, (str, int, float)): print("  %-18s %s" % (k, v))
    elif isinstance(v, list): print("  %-18s list(%d)" % (k, len(v)))
    elif isinstance(v, dict): print("  %-18s dict(%s)" % (k, list(v.keys())[:8]))
# 逐样本
for key in ("samples", "rows", "cases", "results", "details"):
    if key in d and isinstance(d[key], list) and d[key]:
        print("\n=== %s（前 20 条）===" % key)
        for s in d[key][:20]:
            if isinstance(s, dict):
                print("  ", {k: (round(v,3) if isinstance(v,float) else v) for k,v in list(s.items())[:9]})
        break
