# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys, sqlite3, time, statistics, os, traceback
sys.path.insert(0, str(_REPO_ROOT / '作品相关代码' / 'scr'))
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.retrieve.trace import QuoteTracer

RES = []
def rec(cat, name, ok, detail=""):
    RES.append(dict(cat=cat, name=name, ok=bool(ok), detail=str(detail)[:300]))
    print("%-6s [%s] %-40s %s" % ("PASS" if ok else "FAIL", cat, name, str(detail)[:110]))

s = get_settings(); store = Store(s.db_path); store.init_schema()
tr = QuoteTracer(store, s)
con = sqlite3.connect(str(s.db_path))
raw = con.execute("""select t.text from chunks c join chunk_text t on t.chunk_id=c.id
   join documents d on d.id=c.doc_id where d.has_fulltext=1 and length(t.text)>1200 limit 1""").fetchone()[0]

def bench(q, n=5):
    ts=[]
    for _ in range(n):
        t0=time.perf_counter(); tr.trace(q, allow_online=False); ts.append((time.perf_counter()-t0)*1000)
    return statistics.median(ts)

print("--- 空白敏感性 ---")
stored50 = raw[:50]
norm50 = " ".join(raw.split())[:50]
print("  原文前50字含换行:", "\n" in stored50)
t1, t2 = bench(stored50), bench(norm50)
print("  原文前50字       : %.2f ms" % t1)
print("  归一化后前50字   : %.2f ms" % t2)
rec("性能", "原文形态短查询走快路径", t1 < 30, "%.2f ms" % t1)
rec("性能", "空白归一化形态短查询同样快", t2 < 30, "%.2f ms（若>100 说明快路径被空白差异绕过）" % t2)

# 非原样子串
para = " ".join(raw.split())[200:280]
variants = {
  "原样80字": para,
  "改一个词": para.replace(" the ", " a ", 1),
  "加前缀干扰": "extra unrelated words " + para,
  "截取后半段": para[len(para)//2:],
}
for name, q in variants.items():
    t = bench(q)
    print("  %-14s %8.2f ms" % (name, t))
rec("性能", "非原样短查询不再走全量扫描", all(bench(q) < 60 for q in variants.values()),
    {k: round(bench(v),1) for k,v in variants.items()})

# --- 总结合并保护 ---
print("\n--- 总结合并保护 ---")
tmp = os.path.join(str(_REPO_ROOT / '测试资产' / 'data'), "merge_copy.db")
for p in (tmp, tmp+"-wal", tmp+"-shm"):
    try: os.remove(p)
    except Exception: pass
try:
    src = sqlite3.connect(str(s.db_path)); dst = sqlite3.connect(tmp); src.backup(dst); dst.close(); src.close()
    st2 = Store(tmp); st2.init_schema()
    c2 = sqlite3.connect(tmp)
    # 找一篇六字段完整的总结
    target = None
    for did, tldr, prob, meth, find, contrib, lim in c2.execute(
            "select doc_id,tldr,problem,methods,findings,contributions,limitations from summaries"):
        if all(x and str(x).strip() for x in (tldr, prob, meth, find, contrib, lim)):
            target = did; break
    c2.close()
    rec("总结合并", "存在六字段完整的总结用于测试", target is not None, target)
    if target:
        from litagent.summarize.runner import SummaryRunner
        from litagent.schema import Summary, Claim
        before = st2.get_summary(target)
        n_before = before.structured_field_count() if hasattr(before, "structured_field_count") else sum(
            1 for x in (before.tldr,before.problem,before.methods,before.findings,before.contributions,before.limitations) if x and str(x).strip())
        class Stub:
            _cached_calls = 0
            def extract(self, doc, with_claims=True):
                return Summary(doc_id=doc.id, claims=[Claim(text="新论断，用于测试合并")],
                               model="stub", prompt_version="test", content_hash="x")
            def close(self): pass
        runner = SummaryRunner(st2, s); runner._get_extractor = lambda: Stub()
        runner.run(doc_ids=[target])
        after = st2.get_summary(target)
        n_after = after.structured_field_count() if hasattr(after, "structured_field_count") else sum(
            1 for x in (after.tldr,after.problem,after.methods,after.findings,after.contributions,after.limitations) if x and str(x).strip())
        rec("总结合并", "低覆盖度结果不覆盖原有字段", n_after >= n_before,
            "原有 %d 字段 → 现有 %d 字段" % (n_before, n_after))
        rec("总结合并", "新论断被合并进来", len(after.claims) >= 1, "论断数 %d" % len(after.claims))
        rec("总结合并", "原有概述未丢失", bool((after.tldr or "").strip()) == bool((before.tldr or "").strip()),
            "tldr 前=%r 后=%r" % ((before.tldr or "")[:30], (after.tldr or "")[:30]))
    st2.close()
except Exception:
    rec("总结合并", "合并保护", False, traceback.format_exc()[-180:])
finally:
    for p in (tmp, tmp+"-wal", tmp+"-shm"):
        try: os.remove(p)
        except Exception: pass

fails = [r for r in RES if not r["ok"]]
print("\n== 本轮：%d 项，失败 %d ==" % (len(RES), len(fails)))
for f in fails: print("  FAIL:", f["cat"], f["name"], f["detail"][:140])
