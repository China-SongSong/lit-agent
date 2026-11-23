# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import json
p = str(_REPO_ROOT / '作品相关代码' / 'scr' / 'data' / 'evaluation' / 'trace_calibration_v2.json')
d = json.load(open(p, encoding="utf-8"))
print("=== by_split ===")
for split, v in d["by_split"].items():
    print(" ", split, "→", json.dumps(v, ensure_ascii=False)[:300])
print("\n=== cases（全部 %d 条）===" % len(d["cases"]))
for c in d["cases"]:
    print("  ", json.dumps(c, ensure_ascii=False)[:230])
