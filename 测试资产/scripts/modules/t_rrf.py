# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys
sys.path.insert(0, str(_REPO_ROOT / '作品相关代码' / 'scr'))
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.index.hybrid import fuse_rankings
from litagent.retrieve.search import HybridSearcher
from litagent.schema import SearchHit
RES=[]
def rec(no, name, ok, detail=""):
    RES.append(dict(no=no, name=name, ok=bool(ok), detail=str(detail)[:300]))
    print("%-6s #%-2s %-42s %s" % ("PASS" if ok else "FAIL", no, name, str(detail)[:100]))

s = get_settings(); store = Store(s.db_path); store.init_schema()

# 8 两路同命中 > 单路高分
fused = fuse_rankings([[("B",0.99),("A",0.10)], [("A",0.10)]], k=60,
                      source_names=["phrase","bm25"])
order = [h.key for h in fused]
a = next(h for h in fused if h.key=="A"); b = next(h for h in fused if h.key=="B")
rec(8, "两路同命中优先于单路高分", order[0]=="A" and a.score>b.score,
    "顺序=%s A=%.5f B=%.5f" % (order, a.score, b.score))

# 9 权重为 0 跳过、数量不符抛错
f0 = fuse_rankings([[("X",1.0)],[("Y",1.0)]], k=60, weights=[0,1.0], source_names=["phrase","bm25"])
keys = [h.key for h in f0]
rec(9.1, "权重为 0 的路被跳过", keys==["Y"], "结果=%s" % keys)
try:
    fuse_rankings([[("X",1.0)],[("Y",1.0)]], weights=[1.0])
    rec(9.2, "weights 数量不符抛 ValueError", False, "未抛异常")
except ValueError as e:
    rec(9.2, "weights 数量不符抛 ValueError", True, str(e)[:40])
except Exception as e:
    rec(9.2, "weights 数量不符抛 ValueError", False, "%s" % type(e).__name__)

# 10 同名合并保留名次 + top_k 截断
fg = fuse_rankings([[("A",0.9),("B",0.8)], [("A",0.7)]], k=60,
                   source_names=["phrase","bm25"], top_k=1)
h = fg[0]
rec(10.1, "同名文档合并保留各来源名次", h.key=="A" and h.ranks.get("phrase")==1 and h.ranks.get("bm25")==1
    and set(h.sources)=={"phrase","bm25"}, "ranks=%s sources=%s" % (h.ranks, h.sources))
rec(10.2, "top_k 截断生效", len(fg)==1 and len(fuse_rankings([[("A",1),("B",1),("C",1)]], top_k=2))==2,
    "len=%d" % len(fg))

# 11 单路异常，另一路照常
se = HybridSearcher(store, s)
orig_phrase, orig_bm25 = se.phrase.search, se.bm25.search
def boom(*a, **k): raise RuntimeError("模拟指纹路故障")
se.phrase.search = boom
hits, rep = se.search("lithium battery thermal runaway", top_k=5)
rec(11, "单路异常时另一路照常且错误入报告",
    len(hits)>0 and "phrase" in rep.route_errors and rep.routes_used==["bm25"],
    "hits=%d used=%s err=%s" % (len(hits), rep.routes_used, list(rep.route_errors)))
se.phrase.search = orig_phrase

# 12 两路全挂
se.phrase.search = boom
se.bm25.search = boom
hits2, rep2 = se.search("lithium battery", top_k=5)
rec(12, "两路全挂返回空且 routes_used 为空",
    hits2==[] and rep2.routes_used==[] and len(rep2.route_errors)==2,
    "hits=%d used=%s errors=%d" % (len(hits2), rep2.routes_used, len(rep2.route_errors)))
se.phrase.search = orig_phrase; se.bm25.search = orig_bm25

# 13 文献级聚合 +4% 加成
doc = store.get_document(store.connect().execute("select id from documents limit 1").fetchone()[0], with_fulltext=False)
crafted = [SearchHit(chunk_id="c%d"%i, doc_id=doc.id, score=0.5 if i==0 else 0.4-0.01*i, text="t", doc=doc) for i in range(3)]
orig_search = se.search
se.search = lambda q, **kw: (crafted, rep2)
res, _ = se.search_documents("q", top_k=5)
score = res[0][1] if res else 0
rec(13, "文献级聚合三片段加成 +8%", abs(score - 0.5*1.08) < 1e-9,
    "best=0.5 结果=%.4f（期望 0.5400）" % score)
se.search = orig_search

fails=[x for x in RES if not x["ok"]]
print("\n== RRF 与检索降级：%d 项，失败 %d ==" % (len(RES), len(fails)))
for f in fails: print("  FAIL #%s %s %s" % (f["no"], f["name"], f["detail"][:140]))
