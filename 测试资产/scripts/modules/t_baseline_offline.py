# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys, subprocess, sqlite3
sys.path.insert(0, str(_REPO_ROOT / '作品相关代码' / 'scr'))
RES=[]
def rec(no, name, ok, detail=""):
    RES.append(dict(no=no, name=name, ok=bool(ok), detail=str(detail)[:300]))
    print("%-6s #%-2s %-46s %s" % ("PASS" if ok else "FAIL", no, name, str(detail)[:90]))

# 23 基线门禁
out = subprocess.run([sys.executable,"-B","-m","unittest","tests.test_production_baseline","-v"],
                     cwd=str(_REPO_ROOT / '作品相关代码' / 'scr'),
                     capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
rec("23", "生产基线哈希不漂移", out.returncode==0 and "OK" in (out.stderr or ""),
    (out.stderr or "").strip().splitlines()[-1][:80] if (out.stderr or "").strip() else str(out.returncode))

from litagent.config import get_settings
from litagent.index.store import Store
from litagent.retrieve.search import HybridSearcher
from litagent.retrieve.trace import QuoteTracer
s = get_settings(); store = Store(s.db_path); store.init_schema()
con = sqlite3.connect(str(s.db_path))
raw = con.execute("""select t.text from chunks c join chunk_text t on t.chunk_id=c.id
   join documents d on d.id=c.doc_id where d.has_fulltext=1 and length(t.text)>1200 limit 1""").fetchone()[0]
con.close()
quote = " ".join(raw.split())[150:330]

# 24d 溯源正反例
tr = QuoteTracer(store, s)
pos,_ = tr.trace(quote, allow_online=False)
neg,_ = tr.trace("量子纠缠态在超导约瑟夫森结中的退相干时间测量方法研究", allow_online=False)
rec("24d", "端到端溯源正反两例", pos.found and pos.level=="local_exact" and (not neg.found) and neg.level=="none",
    "正例 %s %.3f / 反例 %s" % (pos.level, pos.confidence, neg.level))

# 25 断网降级
import requests
orig = (requests.get, requests.post, requests.Session.request)
def off(*a, **k): raise requests.exceptions.ConnectionError("模拟断网")
requests.get = off; requests.post = off; requests.Session.request = off
try:
    se = HybridSearcher(store, s)
    hits, rep = se.search("lithium battery thermal runaway", top_k=5)
    local_ok = len(hits)>0
    tr2 = QuoteTracer(store, s)
    r_off,_ = tr2.trace(quote, allow_online=False)
    local_trace_ok = r_off.found
    online_failed_gracefully = True; detail = ""
    try:
        r_on,_ = tr2.trace(quote, allow_online=True)
        detail = "联网追溯未抛异常，level=%s" % r_on.level
    except Exception as e:
        online_failed_gracefully = False
        detail = "联网追溯抛出 %s: %s" % (type(e).__name__, str(e)[:60])
    rec("25.1", "离线时本地检索与溯源照常", local_ok and local_trace_ok,
        "检索命中 %d，溯源 %s" % (len(hits), r_off.level))
    rec("25.2", "离线时在线通道优雅失败", online_failed_gracefully, detail)
finally:
    requests.get, requests.post, requests.Session.request = orig

fails=[x for x in RES if not x["ok"]]
print("\n== 基线门禁与降级：%d 项，失败 %d ==" % (len(RES), len(fails)))
for f in fails: print("  FAIL #%s %s %s" % (f["no"], f["name"], f["detail"][:140]))
