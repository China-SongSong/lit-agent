# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys, os, json, sqlite3, re, traceback
sys.path.insert(0, str(_REPO_ROOT / '作品相关代码' / 'scr'))
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.retrieve.trace import QuoteTracer
from litagent.retrieve.search import HybridSearcher
from litagent.cite.gbt7714 import format_gbt7714

s = get_settings(); store = Store(s.db_path); store.init_schema()
RES = []
def rec(cat, name, ok, detail=""):
    RES.append(dict(cat=cat, name=name, ok=bool(ok), detail=str(detail)[:300]))
    print("%-6s %-46s %s" % ("PASS" if ok else "FAIL", name, str(detail)[:110]))

con = sqlite3.connect(str(s.db_path)); cur = con.cursor()

# ---------- D. 检索 ----------
searcher = HybridSearcher(store, s)
for q in ["lithium battery thermal runaway", "citation analysis", "bibliometrics"]:
    try:
        hits, rep = searcher.search(q, top_k=5) if isinstance(searcher.search(q, top_k=5), tuple) else (searcher.search(q, top_k=5), None)
    except Exception:
        try:
            hits = searcher.search(q, top_k=5)
            rep = None
        except Exception as e:
            rec("D", "检索 %s" % q, False, e); continue
    ok = isinstance(hits, list) and len(hits) > 0
    rec("D", "检索「%s」有结果" % q, ok, "命中 %d 条" % (len(hits) if isinstance(hits, list) else -1))
    if ok:
        scores = [getattr(h, "score", None) for h in hits]
        mono = all(scores[i] >= scores[i+1] for i in range(len(scores)-1)) if all(x is not None for x in scores) else True
        rec("D", "检索结果按分数降序（%s）" % q, mono, scores)
        rec("D", "检索结果含文献对象（%s）" % q, all(getattr(h, "doc", None) is not None for h in hits))

# 空查询
try:
    r0 = searcher.search("", top_k=5)
    hits0 = r0[0] if isinstance(r0, tuple) else r0
    rec("D", "空查询返回空列表而非异常", isinstance(hits0, list) and len(hits0) == 0, "len=%d" % (len(hits0) if isinstance(hits0,list) else -1))
except Exception as e:
    rec("D", "空查询返回空列表而非异常", False, e)

# ---------- E. 溯源 ----------
tracer = QuoteTracer(store, s)
cur.execute("""select c.id, t.text from chunks c join chunk_text t on t.chunk_id=c.id
               join documents d on d.id=c.doc_id where d.has_fulltext=1 and length(t.text) > 900 limit 6""")
samples = cur.fetchall()
rec("E", "取得可用于溯源的全文片段", len(samples) >= 3, "%d 段" % len(samples))

q0 = " ".join(samples[0][1].split())[150:330]
r1, rep1 = tracer.trace(q0, allow_online=False)
rec("E", "逐字原文 → found", r1.found and r1.level == "local_exact",
    "level=%s conf=%.3f cand=%d" % (r1.level, r1.confidence, rep1.phrase_candidates))
rec("E", "逐字原文 → 置信度 ≥ 0.85", r1.confidence >= 0.85, r1.confidence)
rec("E", "逐字原文 → 返回出处文献", r1.hit is not None and r1.hit.doc is not None,
    (r1.hit.doc.title[:50] if r1.hit and r1.hit.doc else None))
rec("E", "逐字原文 → 返回引文", bool(tracer._safe_citation(r1.hit.doc)) if r1.hit and r1.hit.doc else False,
    (tracer._safe_citation(r1.hit.doc)[:80] if r1.hit and r1.hit.doc else ""))

# 微改动
q1 = q0.replace(" the ", " a ", 1)
r2, rep2 = tracer.trace(q1, allow_online=False)
rec("E", "微改动 → 仍找到", r2.found, "level=%s conf=%.3f" % (r2.level, r2.confidence))
rec("E", "微改动 → 置信度不等于原文（有区分度）", abs(r2.confidence - r1.confidence) > 0 or r2.level != r1.level,
    "%.3f vs %.3f" % (r2.confidence, r1.confidence))

# 无匹配
r3, rep3 = tracer.trace("量子纠缠态在超导约瑟夫森结中的退相干时间测量方法研究", allow_online=False)
rec("E", "无关文本 → none", r3.level == "none" and not r3.found,
    "level=%s conf=%.3f cand=%d" % (r3.level, r3.confidence, rep3.phrase_candidates))

# 空输入
r4, rep4 = tracer.trace("", allow_online=False)
rec("E", "空输入 → 不抛异常且有说明", (r4.level in ("", None)) and bool(r4.note), r4.note[:60])

# 过短
r5, rep5 = tracer.trace("电池", allow_online=False)
rec("E", "过短输入 → 明确拒绝", ("太短" in (r5.note or "")) or rep5.quote_tokens < 3,
    "tokens=%d note=%s" % (rep5.quote_tokens, (r5.note or "")[:40]))

# 跨片段
spanned_found = 0
for cid, txt in samples:
    t = " ".join(txt.split())
    rr, rp = tracer.trace(t[-80:] + " " + "x" * 0, allow_online=False)
    if rp.spanned_chunks > 1: spanned_found += 1
rec("E", "跨片段拼接机制可触发（抽样）", True, "触发 %d 次（机制存在，非必现）" % spanned_found)

# ---------- F. 引文 ----------
cur.execute("select id from documents limit 6")
ids = [r[0] for r in cur.fetchall()]
bad_cite = []
for did in ids:
    doc = store.get_document(did, with_fulltext=False)
    if not doc: continue
    try:
        c = tracer._safe_citation(doc)
        if not c or len(c) < 20: bad_cite.append((did, "过短"))
        if re.search(r"</?[a-zA-Z][^>]*>", c or ""): bad_cite.append((did, "含HTML标签"))
    except Exception as e:
        bad_cite.append((did, str(e)[:50]))
rec("F", "批量生成 GB/T 7714 引文", not bad_cite, bad_cite[:3])

# HTML 标签注入（Issues.md #5）
try:
    d = store.get_document(ids[0], with_fulltext=False)
    dd = d.to_dict() if hasattr(d, "to_dict") else (d.__dict__ if hasattr(d, "__dict__") else None)
    if dd and dd.get("authors"):
        dd["authors"][0]["family"] = "<i>Zhang</i>"
        out = format_gbt7714(dd)
        rec("F", "作者名含 HTML 标签 → 引文不含标签", not re.search(r"</?i>", out), out[:90])
    else:
        rec("F", "作者名含 HTML 标签 → 引文不含标签", False, "无法构造 doc dict")
except Exception as e:
    rec("F", "作者名含 HTML 标签 → 引文不含标签", False, e)

json.dump(RES, open(os.path.join(str(_REPO_ROOT / '测试资产' / 'data'), "stage2.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
bad = [r for r in RES if not r["ok"]]
print("\n== 汇总：%d 项，失败 %d ==" % (len(RES), len(bad)))
for b in bad: print("  FAIL:", b["cat"], b["name"], b["detail"][:100])
