# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys, tempfile
sys.path.insert(0, str(_REPO_ROOT / '作品相关代码' / 'scr'))
from litagent.evaluation.calibration import build_evaluation_store
FX = Path(str(_REPO_ROOT / '作品相关代码' / 'scr' / 'tests' / 'fixtures'))
work = Path(tempfile.mkdtemp(prefix="ev2_"))
store, settings = build_evaluation_store(FX/"calibration_corpus_v2.jsonl", work)
c = store.connect()
print("%-28s %-8s %s" % ("doc_id","片段数","首片段前 60 字"))
for row in c.execute("""select d.id, count(k.id) from documents d left join chunks k on k.doc_id=d.id group by d.id order by d.id"""):
    did, n = row[0], row[1]
    txt = c.execute("select text from chunk_text t join chunks k on k.id=t.chunk_id where k.doc_id=? limit 1", (did,)).fetchone()
    print("%-28s %-8d %s" % (did, n, (txt[0][:60].replace("\n"," ") if txt else "<无片段>")))
