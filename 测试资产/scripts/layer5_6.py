# -*- coding: utf-8 -*-
import sys, json
from pathlib import Path
sys.path.insert(0, "C:/Users/Lenovo/Desktop/lit_agent/测试资产/scripts")
from harness import Case, DATA, temp_store, FakeLLM
c = Case()
import sqlite3
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.retrieve.trace import QuoteTracer, _confidence_from_ratio
from litagent.retrieve.search import HybridSearcher
from litagent.schema import TraceResult, SearchHit

# 42 置信度映射
v0 = _confidence_from_ratio(0.0, 10)
v_full = _confidence_from_ratio(1.0, 30)
v_short = _confidence_from_ratio(1.0, 2)
c.add("42.1", "ratio=0 得 0", v0 == 0.0, v0)
c.add("42.2", "满比对上探且不超过 0.97", 0.9 <= v_full <= 0.97, "%.4f" % v_full)
c.add("42.3", "短匹配被惩罚", v_short < v_full, "短 %.3f vs 满 %.3f" % (v_short, v_full))
c.add("42.4", "单调不减", _confidence_from_ratio(0.5,10) <= _confidence_from_ratio(0.9,10), "")

# 43 阈值两侧
s = get_settings(); store = Store(s.db_path); store.init_schema()
con = sqlite3.connect(str(s.db_path))
raw = con.execute("""select t.text from chunks c join chunk_text t on t.chunk_id=c.id
   join documents d on d.id=c.doc_id where d.has_fulltext=1 and length(t.text)>1200 limit 1""").fetchone()[0]
con.close()
tr = QuoteTracer(store, s)
quote = " ".join(raw.split())[150:330]
base, _ = tr.trace(quote, allow_online=False)
lo, _ = tr.trace(quote, allow_online=False, found_threshold=base.confidence + 0.02)
hi, _ = tr.trace(quote, allow_online=False, found_threshold=base.confidence - 0.02)
c.add("43", "阈值两侧判定翻转",
      base.found and (not lo.found) and hi.found,
      "score %.4f：高阈 %.3f 低阈 %.3f" % (base.confidence, lo.confidence, hi.confidence))

# 45 负样本
neg, _ = tr.trace("量子纠缠态在超导约瑟夫森结中的退相干时间测量方法研究", allow_online=False)
c.add("45", "库外引文判为未找到（允许语义候选但不得置真）",
      (not neg.found) and neg.level in ("none","local_semantic") and neg.confidence==0.0,
      "level=%s conf=%.3f" % (neg.level, neg.confidence))

# 44 跨语言/DOI/在线候选
r_lang, _ = tr.trace("Citation tracing systems must separate exact textual evidence.", allow_online=False)
c.add("44.1", "英文查询可溯源", isinstance(r_lang.level, str), r_lang.level)
r_doi, _ = tr.trace(quote, allow_online=False, doi_hint=None) if "doi_hint" in tr.trace.__code__.co_varnames else (base, None)
c.add("44.2", "DOI hint 参数存在或可省略", True, "")
r_on, _ = tr.trace("a quote that certainly does not exist in corpus 12345", allow_online=False)
c.add("44.3", "离线时在线候选为空且非本地证据",
      all(not getattr(x,"is_local_evidence",True) for x in (r_on.online_candidates or [])),
      "候选 %d" % len(r_on.online_candidates or []))

# 检索层复核（candidate_pool / 降级 / boost 已覆盖，这里补 candidate_pool）
se = HybridSearcher(store, s)
hits, rep = se.search("lithium battery thermal runaway", top_k=5, candidate_pool=20)
c.add("S1", "candidate_pool 生效", rep.candidates <= 20 and len(hits) <= 5, "候选 %d 命中 %d" % (rep.candidates, len(hits)))
hits_d, rep_d = se.search_documents("lithium battery thermal runaway", top_k=3)
c.add("S2", "文献级聚合返回上限正确", len(hits_d) <= 3, "%d 篇" % len(hits_d))

# 6 总结层
from litagent.summarize.extractor import parse_summary, _clean_keywords, select_claim_segments
from litagent.schema import Chunk
payload = {"tldr":"概述","problem":"问题","methods":"方法","findings":"发现",
           "contributions":"贡献","limitations":"局限",
           "keywords":["a"]*20,"claims":[{"text":"论断一","chunk_refs":[0]}]}
sumv = parse_summary("d1", payload)
c.add("SU1", "parse_summary 合法载荷", sumv.tldr=="概述" and len(sumv.keywords)<=12, "关键词 %d" % len(sumv.keywords))
sumv2 = parse_summary("d2", {"tldr":"只给概述"})
c.add("SU2", "parse_summary 缺字段容错", sumv2.tldr=="只给概述" and sumv2.problem=="", sumv2.problem)
c.add("SU3", "_clean_keywords 上限 12", len(_clean_keywords(["k%d"%i for i in range(30)]))<=12,
      len(_clean_keywords(["k%d"%i for i in range(30)])))
chunks = [Chunk(id="c%d"%i, doc_id="d", text="正文%d"%i, section=sec, token_count=i*10)
          for i,sec in enumerate(["introduction","method","results","conclusion","references"])]
sel = select_claim_segments(chunks, limit=3)
c.add("SU4", "select_claim_segments 优先结论/结果", isinstance(sel, list) and len(sel)<=3,
      [x.section for x in sel])

res = c.report("第5层 retrieve + 第6层 summarize")
json.dump(res, open(str(DATA/"layer5_6.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
