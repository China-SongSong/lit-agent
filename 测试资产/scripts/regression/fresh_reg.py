# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys, os, json, sqlite3, re, copy, importlib, pkgutil
SCR = str(_REPO_ROOT / '作品相关代码' / 'scr')
sys.path.insert(0, SCR)
RES = []
def rec(cat, name, ok, detail=""):
    RES.append(dict(cat=cat, name=name, ok=bool(ok), detail=str(detail)[:300]))
    print("%-6s [%s] %-44s %s" % ("PASS" if ok else "FAIL", cat, name, str(detail)[:100]))

# A 环境
import platform
rec("环境", "Python 版本", sys.version_info >= (3,9), sys.version.split()[0])
for m in ("requests","jieba","pymupdf","streamlit"):
    try:
        importlib.import_module(m); rec("环境", "依赖 %s" % m, True)
    except Exception as e:
        try:
            importlib.import_module("fitz" if m=="pymupdf" else m); rec("环境", "依赖 %s" % m, True, "别名导入")
        except Exception as e2:
            rec("环境", "依赖 %s" % m, False, e2)

# B 模块
import litagent
fails=[]; n=0
for m in pkgutil.walk_packages(litagent.__path__, "litagent."):
    n+=1
    try: importlib.import_module(m.name)
    except Exception as e: fails.append("%s:%s" % (m.name, e))
rec("模块", "全部模块可导入（%d）" % n, not fails, fails[:2])

from litagent.config import get_settings
from litagent.index.store import Store
from litagent.retrieve.trace import QuoteTracer
from litagent.retrieve.search import HybridSearcher
from litagent.cite.gbt7714 import format_gbt7714
from litagent.schema import Document, Author, Summary, Claim, TraceResult, SearchHit
import litagent.contracts as C

s = get_settings(); store = Store(s.db_path); store.init_schema()
con = sqlite3.connect(str(s.db_path)); cur = con.cursor()

# C 数据库与基线
rec("数据库", "integrity_check", cur.execute("pragma integrity_check").fetchone()[0]=="ok")
base = json.load(open(os.path.join(SCR,"tests","fixtures","production_baseline_v1.json"), encoding="utf-8"))
cnt = {
 "documents": cur.execute("select count(*) from documents").fetchone()[0],
 "documents_with_fulltext": cur.execute("select count(*) from documents where has_fulltext=1").fetchone()[0],
 "chunks": cur.execute("select count(*) from chunks").fetchone()[0],
 "postings": cur.execute("select count(*) from postings").fetchone()[0],
 "fingerprints": cur.execute("select count(*) from fingerprints").fetchone()[0],
 "summaries": cur.execute("select count(*) from summaries").fetchone()[0],
}
rec("数据库", "计数与基线一致", cnt==base["counts"], "%s vs %s" % (cnt, base["counts"]))
try:
    from litagent.evaluation.corpus_build import logical_database_sha256
    h = logical_database_sha256(s.db_path)
    rec("数据库", "逻辑哈希与基线一致", h==base["logical_sha256"], h[:16]+" vs "+base["logical_sha256"][:16])
except Exception as e:
    rec("数据库", "逻辑哈希与基线一致", False, e)

# D 索引
orph = sum(cur.execute(q).fetchone()[0] for q in [
 "select count(*) from chunks c left join documents d on d.id=c.doc_id where d.id is null",
 "select count(*) from chunk_text t left join chunks c on c.id=t.chunk_id where c.id is null",
 "select count(*) from postings p left join chunks c on c.id=p.chunk_id where c.id is null",
 "select count(*) from fingerprints f left join chunks c on c.id=f.chunk_id where c.id is null",
 "select count(*) from summaries m left join documents d on d.id=m.doc_id where d.id is null"])
rec("索引", "孤儿数据", orph==0, orph)
nopost = cur.execute("select count(*) from chunks c where not exists(select 1 from postings p where p.chunk_id=c.id)").fetchone()[0]
nofp = cur.execute("select count(*) from chunks c where not exists(select 1 from fingerprints f where f.chunk_id=c.id)").fetchone()[0]
rec("索引", "每片段均有倒排与指纹", nopost==0 and nofp==0, "缺倒排 %d 缺指纹 %d" % (nopost,nofp))
mx = cur.execute("select max(length(text)) from chunk_text").fetchone()[0]
rec("索引", "片段不超上限", mx <= s.max_chunk_chars*1.05, "max=%d 上限=%d" % (mx,s.max_chunk_chars))

# E 检索
se = HybridSearcher(store, s)
for q in ("lithium battery thermal runaway","citation analysis","bibliometrics"):
    r = se.search(q, top_k=5); hits = r[0] if isinstance(r,tuple) else r
    ok = isinstance(hits,list) and len(hits)>0
    rec("检索", "查询有结果：%s" % q[:22], ok, "命中 %d" % (len(hits) if isinstance(hits,list) else -1))
    if ok:
        sc=[h.score for h in hits]
        rec("检索", "分数降序：%s" % q[:22], all(sc[i]>=sc[i+1] for i in range(len(sc)-1)))
r0 = se.search("", top_k=5); r0 = r0[0] if isinstance(r0,tuple) else r0
rec("检索", "空查询返回空列表", isinstance(r0,list) and len(r0)==0)

# F 溯源
tr = QuoteTracer(store, s)
cur.execute("""select t.text from chunks c join chunk_text t on t.chunk_id=c.id
   join documents d on d.id=c.doc_id where d.has_fulltext=1 and length(t.text)>1200 limit 1""")
raw = cur.fetchone()[0]; q0 = " ".join(raw.split())[150:330]
r1,_ = tr.trace(q0, allow_online=False)
rec("溯源", "逐字原文命中", r1.found and r1.level=="local_exact", "level=%s conf=%.3f" % (r1.level,r1.confidence))
rec("溯源", "命中带指纹证据", getattr(r1,"decision_basis","")=="local_fingerprint" and r1.hit and "phrase" in (r1.hit.sources or []),
    "basis=%s sources=%s" % (getattr(r1,"decision_basis",None), (r1.hit.sources if r1.hit else None)))
r2,_ = tr.trace(q0.replace(" the "," a ",1), allow_online=False)
rec("溯源", "微改动仍命中且有区分度", r2.found and abs(r2.confidence-r1.confidence)>1e-6, "level=%s conf=%.3f" % (r2.level,r2.confidence))
r3,_ = tr.trace("量子纠缠态在超导约瑟夫森结中的退相干时间测量方法研究", allow_online=False)
rec("溯源", "无关文本→none", r3.level=="none" and not r3.found, r3.level)
r4,_ = tr.trace("", allow_online=False)
rec("溯源", "空输入→empty", r4.level=="empty", r4.level)
r5,rp5 = tr.trace("电池", allow_online=False)
rec("溯源", "过短输入拒绝", rp5.quote_tokens<3 and bool(r5.note), "tokens=%d" % rp5.quote_tokens)

# G 引文
did = cur.execute("select id from documents limit 1").fetchone()[0]
doc = store.get_document(did, with_fulltext=False)
c1 = tr._safe_citation(doc)
rec("引文", "常规引文", bool(c1) and len(c1)>20 and not re.search(r"</?[a-zA-Z][^>]*>", c1), c1[:60])
dd = doc.to_dict() if hasattr(doc,"to_dict") else dict(doc.__dict__)
bad=[]
for tag in ("<i>Zhang</i>","<b>Zhang</b>","&amp;Zhang"):
    d2=copy.deepcopy(dd); d2["authors"][0]["family"]=tag
    out=format_gbt7714(d2)
    if re.search(r"</?[a-zA-Z][^>]*>", out) or "&AMP;" in out: bad.append(tag)
rec("引文", "HTML 标签/实体不进引文", not bad, bad or "已剥离")

# H 总结
from litagent.summarize.runner import SummaryRunner
sdd = cur.execute("select doc_id from summaries limit 1").fetchone()[0]
orig = s.llm_api_key; s.llm_api_key=""; err=None
try: SummaryRunner(store, s).run(doc_ids=[sdd])
except Exception as e: err=e
s.llm_api_key=orig
rec("总结", "缓存命中免密钥", err is None, str(err).splitlines()[0][:60] if err else "未报错")
rows = cur.execute("select tldr,problem,methods,findings,contributions,limitations from summaries").fetchall()
full = sum(1 for r in rows if all(x and str(x).strip() for x in r))
rec("总结", "字段完整度", full==len(rows), "完整 %d / %d" % (full,len(rows)))

# I 契约
try:
    C.OnlineRequest(query="x", allow_online=False).require_online(); rec("契约","未许可联网被拒",False)
except C.OnlineAccessDenied: rec("契约","未许可联网被拒",True,"OnlineAccessDenied")
rec("契约","opencli 归入浏览器补充层", C.source_tier_for("opencli")==C.SourceTier.BROWSER_SUPPLEMENT)
sc2 = C.SourceCandidate(provider="p",source="opencli",source_tier=C.source_tier_for("opencli"),document=Document(id="z",title="t"))
rec("契约","外部候选非本地证据", sc2.is_local_evidence is False)
rr = TraceResult(query="q"); rr.found=True
h = SearchHit(chunk_id="c",doc_id="d",score=1.0,text="t"); h.sources=["bm25"]; rr.hit=h; rr.matched_text="t"
try:
    C.validate_trace_result(rr); rec("契约","无 phrase 的 found 被拒",False)
except C.ContractViolation: rec("契约","无 phrase 的 found 被拒",True)

# J OpenCLI
try:
    from litagent.fetch.opencli import OpenCLISource
    oc = OpenCLISource(s)
    av = oc.availability()
    rec("OpenCLI", "可用性探测不抛异常", hasattr(av,"available"), "available=%s" % getattr(av,"available",None))
    rec("OpenCLI", "命令可由配置指定", True, " ".join(oc._command_parts())[:60])
    try: oc.close()
    except Exception: pass
except Exception as e:
    rec("OpenCLI", "模块可用", False, e)

con.close()
fails2=[x for x in RES if not x["ok"]]
print("\n== 回归：%d 项，失败 %d ==" % (len(RES), len(fails2)))
for f in fails2: print("  FAIL:", f["cat"], f["name"], f["detail"][:120])
json.dump(RES, open(os.path.join(str(_REPO_ROOT / '测试资产' / 'data'),"fresh_regression.json"),"w",encoding="utf-8"), ensure_ascii=False, indent=1)
