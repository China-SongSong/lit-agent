# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys, os, json, sqlite3, importlib, pkgutil, re, copy, time, traceback
sys.path.insert(0, str(_REPO_ROOT / '作品相关代码' / 'scr'))
RES = []
def rec(cat, name, ok, detail=""):
    RES.append(dict(cat=cat, name=name, ok=bool(ok), detail=str(detail)[:260]))
    print("%-6s [%s] %-44s %s" % ("PASS" if ok else "FAIL", cat, name, str(detail)[:100]))

# ---------- 模块导入 ----------
import litagent
fails = []; n = 0
for m in pkgutil.walk_packages(litagent.__path__, "litagent."):
    n += 1
    try: importlib.import_module(m.name)
    except Exception as e: fails.append("%s: %s" % (m.name, e))
rec("模块", "全部模块可导入（%d）" % n, not fails, "; ".join(fails[:2]))

from litagent.config import get_settings
from litagent.index.store import Store
from litagent.retrieve.trace import QuoteTracer
from litagent.retrieve.search import HybridSearcher
from litagent.cite.gbt7714 import format_gbt7714
from litagent.index.builder import IndexBuilder
from litagent.clean.chunker import chunk_text
from litagent.schema import Document, Author, summary_quality

s = get_settings(); store = Store(s.db_path); store.init_schema()
con = sqlite3.connect(str(s.db_path)); cur = con.cursor()

# ---------- 数据库 ----------
rec("数据库", "integrity_check", cur.execute("pragma integrity_check").fetchone()[0] == "ok")
cnt = {k: cur.execute(q).fetchone()[0] for k, q in {
  "doc": "select count(*) from documents", "chunk": "select count(*) from chunks",
  "post": "select count(*) from postings", "fp": "select count(*) from fingerprints",
  "sum": "select count(*) from summaries"}.items()}
rec("数据库", "计数", cnt["doc"] > 0 and cnt["chunk"] > 0,
    "doc=%d chunk=%d post=%d fp=%d sum=%d" % (cnt["doc"], cnt["chunk"], cnt["post"], cnt["fp"], cnt["sum"]))
orph = sum(cur.execute(q).fetchone()[0] for q in [
  "select count(*) from chunks c left join documents d on d.id=c.doc_id where d.id is null",
  "select count(*) from chunk_text t left join chunks c on c.id=t.chunk_id where c.id is null",
  "select count(*) from postings p left join chunks c on c.id=p.chunk_id where c.id is null",
  "select count(*) from fingerprints f left join chunks c on c.id=f.chunk_id where c.id is null",
  "select count(*) from summaries s left join documents d on d.id=s.doc_id where d.id is null"])
rec("数据库", "孤儿数据合计", orph == 0, orph)
rec("索引", "每片段均有 postings 与 fingerprints",
    cur.execute("select count(*) from chunks c where not exists(select 1 from postings p where p.chunk_id=c.id)").fetchone()[0] == 0
    and cur.execute("select count(*) from chunks c where not exists(select 1 from fingerprints f where f.chunk_id=c.id)").fetchone()[0] == 0)
mx = cur.execute("select max(length(text)) from chunk_text").fetchone()[0]
rec("索引", "片段不超上限", mx <= s.max_chunk_chars * 1.05, "max=%s 上限=%s" % (mx, s.max_chunk_chars))

# ---------- 检索 ----------
searcher = HybridSearcher(store, s)
hits = searcher.search("lithium battery thermal runaway", top_k=5)
hits = hits[0] if isinstance(hits, tuple) else hits
rec("检索", "查询返回结果", isinstance(hits, list) and len(hits) > 0, "命中 %d" % len(hits))
sc = [getattr(h, "score", 0) for h in hits]
rec("检索", "分数降序", all(sc[i] >= sc[i+1] for i in range(len(sc)-1)), [round(x,4) for x in sc[:3]])
r0 = searcher.search("", top_k=5); r0 = r0[0] if isinstance(r0, tuple) else r0
rec("检索", "空查询不抛异常", isinstance(r0, list) and len(r0) == 0)

# ---------- 溯源 ----------
tracer = QuoteTracer(store, s)
cur.execute("""select t.text from chunks c join chunk_text t on t.chunk_id=c.id
               join documents d on d.id=c.doc_id where d.has_fulltext=1 and length(t.text)>900 limit 1""")
q0 = " ".join(cur.fetchone()[0].split())[150:330]
r1, p1 = tracer.trace(q0, allow_online=False)
rec("溯源", "逐字原文", r1.found and r1.level == "local_exact", "level=%s conf=%.3f" % (r1.level, r1.confidence))
def mutate_quote(text):
    for old,new in ((" the "," a "),(" and "," or "),(" of "," in "),(" to "," from ")):
        if old in text:
            return text.replace(old,new,1)
    words=text.split()
    for index,word in enumerate(words):
        if len(word)>=5 and word.isalpha():
            words[index]=word[:-1]+"x"
            return " ".join(words)
    return text

q1 = mutate_quote(q0)
rec("溯源", "微改动样例确实发生变化", q1 != q0, q1[:100])
r2, _ = tracer.trace(q1, allow_online=False)
rec("溯源", "微改动仍找到且有区分度", r2.found and r2.confidence != r1.confidence,
    "level=%s conf=%.3f" % (r2.level, r2.confidence))
r3, p3 = tracer.trace("量子纠缠态在超导约瑟夫森结中的退相干时间测量方法研究", allow_online=False)
rec("溯源", "无关文本不确认出处",
    r3.level in ("none","local_semantic") and not r3.found and r3.confidence == 0,
    "level=%s" % r3.level)

# 回归：空输入等级（原缺陷 6）
r4, _ = tracer.trace("", allow_online=False)
rec("溯源", "空输入等级为 empty（回归）", r4.level == "empty", "level=%r note=%r" % (r4.level, r4.note))
r5, p5 = tracer.trace("电池", allow_online=False)
rec("溯源", "过短输入拒绝", p5.quote_tokens < 3 and bool(r5.note), "tokens=%d" % p5.quote_tokens)

# ---------- 引文（回归：HTML 标签） ----------
did = cur.execute("select id from documents limit 1").fetchone()[0]
doc = store.get_document(did, with_fulltext=False)
dd = doc.to_dict() if hasattr(doc, "to_dict") else dict(doc.__dict__)
bad = []
for tag in ["<i>Zhang</i>", "<b>Zhang</b>", "Zhang<sup>1</sup>", "&amp;Zhang"]:
    d2 = copy.deepcopy(dd); d2["authors"][0]["family"] = tag
    out = format_gbt7714(d2)
    if re.search(r"</?[a-zA-Z][^>]*>", out) or "&AMP;" in out or "&amp;" in out:
        bad.append((tag, out[:40]))
rec("引文", "HTML 标签/实体不进引文（回归）", not bad, bad[:2] if bad else "全部已剥离")
c = tracer._safe_citation(doc)
rec("引文", "常规引文生成", bool(c) and len(c) > 20 and not re.search(r"</?[a-zA-Z][^>]*>", c), c[:60])

# ---------- 总结（回归：缓存免密钥） ----------
from litagent.summarize.runner import SummaryRunner
sdid = cur.execute("select doc_id from summaries limit 1").fetchone()[0]
orig = s.llm_api_key
s.llm_api_key = ""
err = None
try:
    SummaryRunner(store, s).run(doc_ids=[sdid])
except Exception as e:
    err = e
s.llm_api_key = orig
rec("总结", "缓存命中免密钥（回归）", err is None, str(err).splitlines()[0][:80] if err else "未报错")
summary_ids=[r[0] for r in cur.execute("select doc_id from summaries").fetchall()]
qualities=[]
for doc_id in summary_ids:
    summary=store.get_summary(doc_id)
    document=store.get_document(doc_id,with_fulltext=False)
    content_chars=cur.execute(
        "select length(text) from documents_fulltext where doc_id=?",(doc_id,)
    ).fetchone()
    state,_=summary_quality(
        summary,document,
        content_chars=content_chars[0] if content_chars else 0,
    ) if summary is not None and document is not None else ("missing",[])
    qualities.append(state)
acceptable={"complete","title_limited","abstract_limited","short_content_limited","claims_only"}
rec("总结", "结构化字段完整度按内容级别判定",
    bool(qualities) and all(state in acceptable for state in qualities),
    qualities)

json.dump(RES, open(os.path.join(str(_REPO_ROOT / '测试资产' / 'data'), "retest_core.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
bad2 = [r for r in RES if not r["ok"]]
print("\n== 核心：%d 项，失败 %d ==" % (len(RES), len(bad2)))
for b in bad2: print("  FAIL:", b["cat"], b["name"], b["detail"][:130])
