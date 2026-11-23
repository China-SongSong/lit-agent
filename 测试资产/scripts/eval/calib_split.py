# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import json
p = str(_REPO_ROOT / '作品相关代码' / 'scr' / 'data' / 'evaluation' / 'trace_calibration_v2.json')
d = json.load(open(p, encoding="utf-8"))
for split in ("calibration", "test"):
    print("=== %s 拆分 ===" % split)
    print("%-7s %-5s %-9s %-9s %-7s %-9s %s" % ("阈值","样本","正确确认","错误确认","漏报","正确拒绝","召回"))
    for row in d["by_split"][split]:
        print("%-7.2f %-5d %-9d %-9d %-7d %-9d %.3f" % (
            row["threshold"], row["total"], row["correct_found"], row["wrong_found"],
            row["missed"], row["correct_abstain"], row["recall"]))
    print()
print("推荐阈值:", d["recommended_threshold"])
print("策略:", d["recommendation_policy"])
