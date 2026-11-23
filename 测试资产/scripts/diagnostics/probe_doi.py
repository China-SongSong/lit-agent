# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys
sys.path.insert(0, str(_REPO_ROOT / '作品相关代码' / 'scr'))
from litagent.schema import normalize_doi
for v in ["10.1000/ABC/", "10.1000/ABC", "dois.org/10.1000/ABC", "https://dx.doi.org/10.1000/ABC",
          " 10.1000/ABC ", "10.1000/ABC.", "", None, "not-a-doi"]:
    print("%-32r -> %r" % (v, normalize_doi(v)))
