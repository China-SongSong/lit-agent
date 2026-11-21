# -*- coding: utf-8 -*-
import sys, sqlite3, time, statistics, copy
sys.path.insert(0, r"C:\Users\Lenovo\Desktop\lit_agent\作品相关代码\scr")
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.retrieve.trace import QuoteTracer
import litagent.contracts as C
from litagent.schema import TraceResult, SearchHit

RES = []
def rec(cat, name, ok, detail=""):
    RES.append(dict(cat=cat, name=name, ok=bool(ok), detail=str(detail)[:300]))
    print("%-6s [%s] %-42s %s" % ("PASS" if ok else "FAIL", cat, name, str(detail)[:110]))

s = get_settings(); store = Store(s.db_path); store.init_schema()
tr = QuoteTracer(store, s)
con = sqlite3.connect(str(s.db_path))

# --- 契约：联网许可 ---
try:
    C.OnlineRequest(query="x", allow_online=False).require_online()
    rec("契约", "未许可联网被拒绝", False, "竟然通过了")
except C.OnlineAccessDenied:
    rec("契约", "未许可联网被拒绝", True, "OnlineAccessDenied")
except Exception as e:
    rec("契约", "未许可联网被拒绝", False, e)
try:
    C.OnlineRequest(query="x", allow_online=True).require_online()
    rec("契约", "已许可联网放行", True)
except Exception as e:
    rec("契约", "已许可联网放行", False, e)

# --- 契约：候选不得冒充本地证据 ---
from litagent.schema import Document
sc = C.SourceCandidate(provider="p", source="opencli", source_tier=C.source_tier_for("opencli"),
                       document=Document(id="z", title="t"))
rec("契约", "外部候选 is_local_evidence 恒为假", sc.is_local_evidence is False)
rec("契约", "opencli 归入浏览器补充层", sc.source_tier == C.SourceTier.BROWSER_SUPPLEMENT, sc.source_tier.value)
rec("契约", "openalex 归入主通道", C.source_tier_for("openalex") == C.SourceTier.PRIMARY_API)
rec("契约", "未知来源不获得本地权限", C.source_tier_for("random") == C.SourceTier.EXTERNAL_OTHER)

# --- 契约：found 必须来自 phrase ---
r = TraceResult(query="q"); r.found = True
hit = SearchHit(chunk_id="c", doc_id="d", score=1.0, text="t")
hit.sources = ["bm25"]
r.hit = hit
try:
    C.validate_trace_result(r)
    rec("契约", "无 phrase 来源的 found 被拒绝", False, "竟然通过了")
except C.ContractViolation as e:
    rec("契约", "无 phrase 来源的 found 被拒绝", True, str(e)[:60])
except Exception as e:
    rec("契约", "无 phrase 来源的 found 被拒绝", False, e)

hit2 = copy.deepcopy(hit); hit2.sources = ["phrase"]
r2 = TraceResult(query="q"); r2.found = True; r2.hit = hit2
try:
    C.validate_trace_result(r2)
    rec("契约", "带 phrase 来源的 found 被接受", True)
except Exception as e:
    rec("契约", "带 phrase 来源的 found 被接受", False, e)

# --- 生产路径是否真的调用契约校验 ---
import subprocess
g = subprocess.run([sys.executable, "-c",
  "import re,sys; s=open(r'%s','r',encoding='utf-8').read(); print(len(re.findall('validate_trace_result', s)))"
  % (r"C:\Users\Lenovo\Desktop\lit_agent\作品相关代码\scr\litagent\retrieve\trace.py")],
  capture_output=True, text=True)
rec("契约", "溯源路径接入契约校验", g.stdout.strip() not in ("0", ""), "trace.py 中出现 %s 次" % g.stdout.strip())

# --- 离线溯源不得联网 ---
raw = con.execute("""select t.text from chunks c join chunk_text t on t.chunk_id=c.id
   join documents d on d.id=c.doc_id where d.has_fulltext=1 and length(t.text)>1200 limit 1""").fetchone()[0]
q = " ".join(raw.split())[150:330]
rr, rp = tr.trace(q, allow_online=False)
rec("契约", "离线溯源不发起联网", rp.online_queried is False and not rr.online_candidates,
    "联网=%s 候选=%d" % (rp.online_queried, len(rr.online_candidates)))

# --- 空白差异的短查询是否已提速 ---
def bench(qq, n=5):
    ts=[]
    for _ in range(n):
        t0=time.perf_counter(); tr.trace(qq, allow_online=False); ts.append((time.perf_counter()-t0)*1000)
    return statistics.median(ts)
stored = raw[:50]; norm = " ".join(raw.split())[:50]
rec("性能", "原文形态短查询", bench(stored) < 30, "%.2f ms" % bench(stored))
rec("性能", "空白归一化短查询", bench(norm) < 30, "%.2f ms" % bench(norm))

# --- 评测语料 v2 ---
try:
    c2 = sqlite3.connect(r"C:\Users\Lenovo\Desktop\lit_agent\作品相关代码\scr\data\evaluation\corpus_v2\index.db")
    print("\n--- 评测语料 v2 ---")
    print("   文献=%d 片段=%d 含全文=%d 指纹=%d" % (
        c2.execute("select count(*) from documents").fetchone()[0],
        c2.execute("select count(*) from chunks").fetchone()[0],
        c2.execute("select count(*) from documents where has_fulltext=1").fetchone()[0],
        c2.execute("select count(*) from fingerprints").fetchone()[0]))
    c2.close()
except Exception as e:
    print("   评测库读取失败:", e)

fails = [r for r in RES if not r["ok"]]
print("\n== 契约与性能：%d 项，失败 %d ==" % (len(RES), len(fails)))
for f in fails: print("  FAIL:", f["cat"], f["name"], f["detail"][:130])
