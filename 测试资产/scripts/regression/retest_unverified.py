# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys, os, json, sqlite3, copy, time, traceback
sys.path.insert(0, str(_REPO_ROOT / '作品相关代码' / 'scr'))
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.retrieve.trace import QuoteTracer
from litagent.schema import split_cjk_name, Summary
from litagent.summarize.runner import SummaryRunner

RES = []
def rec(item, name, ok, detail=""):
    RES.append(dict(item=item, name=name, ok=bool(ok), detail=str(detail)[:300]))
    print("%-6s [%s] %-38s %s" % ("PASS" if ok else "FAIL", item, name, str(detail)[:110]))

s = get_settings(); store = Store(s.db_path); store.init_schema()
con = sqlite3.connect(str(s.db_path)); cur = con.cursor()

# ============ DEF-013 复合中文姓氏 ============
cases = {"欧阳修": ("欧阳","修"), "司马光": ("司马","光"), "诸葛亮": ("诸葛","亮"),
         "上官婉儿": ("上官","婉儿"), "尉迟恭": ("尉迟","恭"), "张三": ("张","三"),
         "王小明": ("王","小明"), "李": ("李","")}
bad = []
for name, exp in cases.items():
    got = split_cjk_name(name)
    if tuple(got) != exp: bad.append((name, got, exp))
rec("DEF-013", "复姓拆分（%d 例）" % len(cases), not bad, bad[:3] if bad else "全部正确")

# 端到端：含复姓作者的引文
from litagent.cite.gbt7714 import format_gbt7714
did0 = cur.execute("select id from documents limit 1").fetchone()[0]
d0 = store.get_document(did0, with_fulltext=False)
dd = d0.to_dict() if hasattr(d0,"to_dict") else dict(d0.__dict__)
dd2 = copy.deepcopy(dd)
dd2["authors"] = [{"family":"欧阳","given":"修"}, {"family":"司马","given":"光"}]
dd2["language"] = "zh"
out = format_gbt7714(dd2)
rec("DEF-013", "复姓作者引文输出", "欧阳" in out and "司马" in out, out[:70])

# ============ DEF-009 局部命中字符偏移 ============
tracer = QuoteTracer(store, s)
cur.execute("""select t.text from chunks c join chunk_text t on t.chunk_id=c.id
               join documents d on d.id=c.doc_id where d.has_fulltext=1 and length(t.text)>1400 limit 3""")
rows = cur.fetchall()
checked = 0; mism = []
for (raw,) in rows:
    orig = " ".join(raw.split())
    if len(orig) < 900: continue
    q = orig[300:520]                      # 只取后半段
    r, rp = tracer.trace(q, allow_online=False)
    if not r.found or not r.matched_text: continue
    checked += 1
    m = " ".join(r.matched_text.split())
    idx = orig.find(m)
    if idx < 0:
        mism.append(("matched_text 不在原文", m[:40])); continue
    cb = " ".join((r.context_before or "").split())
    ca = " ".join((r.context_after or "").split())
    okb = (not cb) or orig[:idx].endswith(cb[-40:]) if cb else True
    oka = (not ca) or orig[idx+len(m):].startswith(ca[:40]) if ca else True
    if not (okb and oka):
        mism.append(("上下文偏移不符", "idx=%d cb=%r ca=%r" % (idx, cb[-30:], ca[:30])))
rec("DEF-009", "局部命中上下文偏移（%d 段）" % checked, checked > 0 and not mism,
    mism[:2] if mism else "matched_text 与上下文在原文中前后衔接")

# ============ DEF-010 弱命中 + 联网候选 ============
cur.execute("select t.text from chunks c join chunk_text t on t.chunk_id=c.id join documents d on d.id=c.doc_id where d.has_fulltext=1 and length(t.text)>900 limit 1")
base = " ".join(cur.fetchone()[0].split())[100:300]
weak_q = base + " " + " ".join(["furthermore"] * 16)
t0 = time.monotonic()
rw, rpw = tracer.trace(weak_q, allow_online=True)
dt = time.monotonic() - t0
rec("DEF-010", "弱命中等级为 local_weak", rw.level == "local_weak", "level=%s conf=%.3f" % (rw.level, rw.confidence))
rec("DEF-010", "弱命中时联网候选未被抑制",
    bool(rw.online_candidates) or rpw.online_queried,
    "联网=%s 候选=%d err=%s 耗时%.1fs" % (rpw.online_queried, len(rw.online_candidates), (rpw.online_error or "")[:60], dt))

# ============ DEF-011 压缩率统计口径 ============
tmp = os.path.join(str(_REPO_ROOT / '测试资产' / 'data'), "clean_copy.db")
for p in (tmp, tmp+"-wal", tmp+"-shm"):
    try: os.remove(p)
    except Exception: pass
try:
    src = sqlite3.connect(str(s.db_path)); dst = sqlite3.connect(tmp); src.backup(dst); dst.close(); src.close()
    st2 = Store(tmp); st2.init_schema()
    c2 = sqlite3.connect(tmp)
    row = c2.execute("""select id from documents where has_fulltext=1 and (pdf_path is null or pdf_path='') limit 1""").fetchone()
    if row is None:
        row = c2.execute("select id from documents where has_fulltext=1 limit 1").fetchone()
    tid = row[0]; c2.close()
    doc = st2.get_document(tid, with_fulltext=True)
    raw_len = len(doc.fulltext or "")
    from litagent.clean.pipeline import CleanPipeline
    rep = CleanPipeline(st2, s).run(doc_ids=[tid], redo=True)
    ok_before = rep.chars_before == raw_len
    # 用实际清洗结果反算
    after_doc = st2.get_document(tid, with_fulltext=True)
    ok_after = rep.chars_after == len(after_doc.fulltext or "")
    expect = "%+.1f%%" % (100.0*(rep.chars_after-rep.chars_before)/rep.chars_before) if rep.chars_before else "-"
    rec("DEF-011", "chars_before = 清洗前原始字符数", ok_before, "report=%d 实际=%d" % (rep.chars_before, raw_len))
    rec("DEF-011", "chars_after = 清洗后字符数", ok_after, "report=%d 实际=%d" % (rep.chars_after, len(after_doc.fulltext or "")))
    rec("DEF-011", "压缩比计算与记录值一致", rep.compression == expect, "%s vs %s" % (rep.compression, expect))
    st2.close()
except Exception:
    rec("DEF-011", "压缩率口径", False, traceback.format_exc()[-160:])
finally:
    for p in (tmp, tmp+"-wal", tmp+"-shm"):
        try: os.remove(p)
        except Exception: pass

# ============ DEF-001 summarize --limit 计数 ============
tmp2 = os.path.join(str(_REPO_ROOT / '测试资产' / 'data'), "sum_copy.db")
for p in (tmp2, tmp2+"-wal", tmp2+"-shm"):
    try: os.remove(p)
    except Exception: pass
try:
    src = sqlite3.connect(str(s.db_path)); dst = sqlite3.connect(tmp2); src.backup(dst); dst.close(); src.close()
    st3 = Store(tmp2); st3.init_schema()
    # 制造待办：删掉 2 篇总结，使待总结数 > limit
    c3 = sqlite3.connect(tmp2)
    victim = [r[0] for r in c3.execute("select doc_id from summaries limit 2")]
    c3.executemany("delete from summaries where doc_id=?", [(v,) for v in victim]); c3.commit()
    total_ft = c3.execute("select count(*) from documents where has_fulltext=1").fetchone()[0]
    c3.close()
    pending = st3.docs_needing_summary("summary-v1", only_with_fulltext=True)
    class Stub:
        _cached_calls = 0
        def extract(self, doc, with_claims=True):
            return Summary(doc_id=doc.id, tldr="stub", problem="p", methods="m",
                           findings="f", contributions="c", limitations="l",
                           model="stub", prompt_version="test", content_hash="x")
        def close(self): pass
    runner = SummaryRunner(st3, s)
    runner._get_extractor = lambda: Stub()
    r1 = runner.run(limit=1)
    rec("DEF-001", "limit=1 时 total 等于实处理数", r1.total == 1, "total=%d" % r1.total)
    rec("DEF-001", "超出 limit 的计入 deferred 而非 skipped",
        r1.deferred == max(0, len(pending)-1), "deferred=%d 待办=%d" % (r1.deferred, len(pending)))
    rec("DEF-001", "skipped 只统计已总结过的",
        r1.skipped == max(0, total_ft - len(pending)), "skipped=%d 全文=%d 待办=%d" % (r1.skipped, total_ft, len(pending)))
    rec("DEF-001", "三项互不重叠且覆盖全部全文文献",
        r1.total + r1.deferred + r1.skipped == total_ft,
        "%d+%d+%d=%d vs %d" % (r1.total, r1.deferred, r1.skipped, r1.total+r1.deferred+r1.skipped, total_ft))
    st3.close()
except Exception:
    rec("DEF-001", "summarize 计数", False, traceback.format_exc()[-160:])
finally:
    for p in (tmp2, tmp2+"-wal", tmp2+"-shm"):
        try: os.remove(p)
        except Exception: pass

fails = [r for r in RES if not r["ok"]]
print("\n== 未验证项补测：%d 项，失败 %d ==" % (len(RES), len(fails)))
for f in fails: print("  FAIL:", f["item"], f["name"], f["detail"][:140])
json.dump(RES, open(os.path.join(str(_REPO_ROOT / '测试资产' / 'data'),"retest_unverified.json"),"w",encoding="utf-8"), ensure_ascii=False, indent=1)
