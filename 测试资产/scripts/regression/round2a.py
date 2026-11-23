# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys, os, json, sqlite3, time, threading
sys.path.insert(0, str(_REPO_ROOT / '作品相关代码' / 'scr'))
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.retrieve.trace import QuoteTracer
from litagent.retrieve.search import HybridSearcher

RES = []
def rec(cat, name, ok, detail=""):
    RES.append(dict(cat=cat, name=name, ok=bool(ok), detail=str(detail)[:300]))
    print("%-6s [%s] %-42s %s" % ("PASS" if ok else "FAIL", cat, name, str(detail)[:110]))

s = get_settings(); store = Store(s.db_path); store.init_schema()
tracer = QuoteTracer(store, s); searcher = HybridSearcher(store, s)
con = sqlite3.connect(str(s.db_path)); cur = con.cursor()
def snap():
    return tuple(cur.execute(q).fetchone()[0] for q in [
        "select count(*) from documents", "select count(*) from chunks",
        "select count(*) from postings", "select count(*) from fingerprints"])

print("--- 检索异常输入 ---")
PUNCT = "!@#$%^&*()_+-=[]{};:'\"" + ",.<>/?" + chr(92) + "|~" + chr(96)
cases = {
    "超长查询(10000字)": "lithium battery " * 700,
    "纯标点": PUNCT,
    "SQL 注入样式": "'; DROP TABLE documents; --",
    "引号与通配符": "%' OR '1'='1",
    "纯空白": "   \t\n  ",
    "emoji": "battery thermal runaway hot",
    "中英混排": "锂电池 thermal runaway 预警 early warning",
    "单字符": "a",
}
before = snap()
for name, q in cases.items():
    try:
        r = searcher.search(q, top_k=5)
        hits = r[0] if isinstance(r, tuple) else r
        rec("检索", name, isinstance(hits, list), "返回 %d 条" % (len(hits) if isinstance(hits,list) else -1))
    except Exception as e:
        rec("检索", name, False, "%s: %s" % (type(e).__name__, str(e)[:80]))
after = snap()
rec("检索", "异常输入后数据库未被破坏", before == after, "%s -> %s" % (before, after))
rec("数据库", "注入尝试后表仍在", cur.execute("select count(*) from sqlite_master where name='documents'").fetchone()[0] == 1)

print("--- 溯源边界输入 ---")
cur.execute("select t.text from chunks c join chunk_text t on t.chunk_id=c.id join documents d on d.id=c.doc_id where d.has_fulltext=1 and length(t.text)>1200 limit 1")
raw = " ".join(cur.fetchone()[0].split())
tests = {
    "仅1个字符": "a",
    "恰好6字符": "abcdef",
    "空白串": "     ",
    "纯标点": "......????",
    "超长输入(约5万字)": raw[:800] * 60,
    "重复同一句20次": (" ".join(raw[200:400].split()) + " ") * 20,
    "原文首80字": raw[:80],
    "原文末尾80字": raw[-80:],
}
for name, q in tests.items():
    try:
        t0 = time.perf_counter(); r, rp = tracer.trace(q, allow_online=False); dt = time.perf_counter()-t0
        rec("溯源", name, isinstance(r.level, str) and r.level != "",
            "level=%-11s conf=%.3f tokens=%d %.2fs" % (r.level, r.confidence, rp.quote_tokens, dt))
    except Exception as e:
        rec("溯源", name, False, "%s: %s" % (type(e).__name__, str(e)[:80]))

print("--- 并发一致性 ---")
q = raw[150:330]
solo, _ = tracer.trace(q, allow_online=False)
results = []; errs = []
def worker():
    try:
        rr, _ = QuoteTracer(store, s).trace(q, allow_online=False)
        results.append((rr.level, round(rr.confidence,3), rr.hit.chunk_id if rr.hit else None))
    except Exception as e:
        errs.append(str(e)[:60])
ths = [threading.Thread(target=worker) for _ in range(8)]
t0 = time.perf_counter()
for t in ths: t.start()
for t in ths: t.join()
dt = time.perf_counter()-t0
rec("并发", "8 线程并发溯源无异常", not errs, errs[:2] or "8/8 成功")
rec("并发", "并发结果与单次一致", len(set(results)) == 1 and results[0][0] == solo.level,
    "唯一结果组数=%d 耗时%.2fs" % (len(set(results)), dt))

vals = []
for _ in range(10):
    rr, _ = tracer.trace(q, allow_online=False)
    vals.append((rr.level, round(rr.confidence,4)))
rec("稳定性", "同查询 10 次结果一致", len(set(vals)) == 1, list(set(vals))[:2])

print("--- 性能 ---")
perf = []
for L in (50, 200, 800, 3000, 12000):
    qq = (raw[:L] if L <= len(raw) else (raw * 20)[:L])
    ts = []
    for _ in range(5):
        t0 = time.perf_counter(); tracer.trace(qq, allow_online=False); ts.append((time.perf_counter()-t0)*1000)
    ts.sort(); perf.append((L, ts[len(ts)//2]))
    print("   输入 %5d 字 -> 中位 %6.2f ms" % (L, ts[len(ts)//2]))
rec("性能", "溯源耗时随输入增长可控（<500ms @12000字）", perf[-1][1] < 500, "%.2f ms @ %d 字" % (perf[-1][1], perf[-1][0]))

fails = [r for r in RES if not r["ok"]]
print("\n== 第二轮A：%d 项，失败 %d ==" % (len(RES), len(fails)))
for f in fails: print("  FAIL:", f["cat"], f["name"], f["detail"][:130])
json.dump(RES, open(os.path.join(str(_REPO_ROOT / '测试资产' / 'data'),"round2a.json"),"w",encoding="utf-8"), ensure_ascii=False, indent=1)
