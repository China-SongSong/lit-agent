# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys, sqlite3, dataclasses
sys.path.insert(0, str(_REPO_ROOT / '作品相关代码' / 'scr'))
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.retrieve.trace import QuoteTracer
import litagent.contracts as C
from litagent.schema import TraceResult, SearchHit

print("=== TraceResult 新增字段 ===")
for f in dataclasses.fields(TraceResult):
    if f.name in ("decision_basis", "found", "level", "hit"):
        print("  %-16s default=%r" % (f.name, f.default))

s = get_settings(); store = Store(s.db_path); store.init_schema()
tr = QuoteTracer(store, s)
con = sqlite3.connect(str(s.db_path))
raw = con.execute("""select t.text from chunks c join chunk_text t on t.chunk_id=c.id
   join documents d on d.id=c.doc_id where d.has_fulltext=1 and length(t.text)>1200 limit 1""").fetchone()[0]
r, rp = tr.trace(" ".join(raw.split())[150:330], allow_online=False)
print("\n=== 生产路径的命中结果 ===")
print("  found=%s level=%s conf=%.4f" % (r.found, r.level, r.confidence))
print("  decision_basis=%r" % getattr(r, "decision_basis", "字段不存在"))
print("  hit.sources=%r" % (getattr(r.hit, "sources", None) if r.hit else None))
try:
    C.validate_trace_result(r)
    print("  契约校验: 通过")
except Exception as e:
    print("  契约校验: 失败 ->", e)

# 正确构造一个合规结果，验证校验器会接受
r2 = TraceResult(query="q"); r2.found = True
h2 = SearchHit(chunk_id="c", doc_id="d", score=1.0, text="t"); h2.sources = ["phrase"]
r2.hit = h2; r2.decision_basis = C.DecisionBasis.LOCAL_FINGERPRINT
try:
    C.validate_trace_result(r2); print("\n  构造合规结果: 校验通过")
except Exception as e:
    print("\n  构造合规结果: 仍被拒 ->", e)
# 缺匹配文本
r3 = TraceResult(query="q"); r3.found = True; r3.hit = h2; r3.decision_basis = C.DecisionBasis.LOCAL_FINGERPRINT
r3.matched_text = ""
try:
    C.validate_trace_result(r3); print("  无匹配文本: 竟然通过（应有拦截）")
except Exception as e:
    print("  无匹配文本: 被拒 ->", str(e)[:60])
