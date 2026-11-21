# -*- coding: utf-8 -*-
import sys, os, sqlite3, time, traceback
sys.path.insert(0, r"C:\Users\Lenovo\Desktop\lit_agent\作品相关代码\scr")
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.retrieve.trace import QuoteTracer
from litagent.retrieve.search import HybridSearcher
from litagent.index.builder import IndexBuilder

RES = []
def rec(cat, name, ok, detail=""):
    RES.append(dict(cat=cat, name=name, ok=bool(ok), detail=str(detail)[:300]))
    print("%-6s [%s] %-40s %s" % ("PASS" if ok else "FAIL", cat, name, str(detail)[:110]))

s = get_settings()

# --- 1. 重建后溯源（用存活文献，在副本上） ---
tmp = os.path.join(r"C:/Users/Lenovo/Desktop/lit_agent/测试资产/data", "rb.db")
for p in (tmp, tmp+"-wal", tmp+"-shm"):
    try: os.remove(p)
    except Exception: pass
src = sqlite3.connect(str(s.db_path)); dst = sqlite3.connect(tmp); src.backup(dst); dst.close(); src.close()
st = Store(tmp); st.init_schema(); tr = QuoteTracer(st, s)
c = sqlite3.connect(tmp)
q, docid = None, None
for did, txt in c.execute("""select c.doc_id, t.text from chunks c join chunk_text t on t.chunk_id=c.id
                              join documents d on d.id=c.doc_id where d.has_fulltext=1 and length(t.text)>1200 limit 3"""):
    q = " ".join(txt.split())[150:330]; docid = did; break
r0, _ = tr.trace(q, allow_online=False)
rec("索引重建", "重建前可命中", r0.found)
t0 = time.time(); IndexBuilder(st, s).build_lexical(rebuild=True); dt = time.time()-t0
r1, _ = tr.trace(q, allow_online=False)
rec("索引重建", "重建后仍可命中且结论一致", r1.found and r1.level == r0.level and abs(r1.confidence - r0.confidence) < 1e-6,
    "前 level=%s conf=%.4f → 后 level=%s conf=%.4f (%.1fs)" % (r0.level, r0.confidence, r1.level, r1.confidence, dt))
c.close(); st.close()
for p in (tmp, tmp+"-wal", tmp+"-shm"):
    try: os.remove(p)
    except Exception: pass

# --- 2. 空数据库上的健壮性 ---
empty = os.path.join(r"C:/Users/Lenovo/Desktop/lit_agent/测试资产/data", "empty.db")
for p in (empty, empty+"-wal", empty+"-shm"):
    try: os.remove(p)
    except Exception: pass
try:
    es = Store(empty); es.init_schema()
    stt = es.stats()
    rec("空库", "空库 stats 可返回", isinstance(stt, dict), "文献=%s" % stt.get("documents"))
    try:
        h = HybridSearcher(es, s).search("battery", top_k=5)
        h = h[0] if isinstance(h, tuple) else h
        rec("空库", "空库检索返回空列表", isinstance(h, list) and len(h) == 0, "返回 %d" % (len(h) if isinstance(h,list) else -1))
    except Exception as e:
        rec("空库", "空库检索返回空列表", False, e)
    try:
        rr, rp = QuoteTracer(es, s).trace("some quoted text here", allow_online=False)
        rec("空库", "空库溯源不抛异常", rr.level in ("none","empty") and not rr.found, "level=%s" % rr.level)
    except Exception as e:
        rec("空库", "空库溯源不抛异常", False, e)
    try:
        rep = IndexBuilder(es, s).build_lexical(rebuild=True)
        rec("空库", "空库建索引不抛异常", True, "处理 %d 篇" % getattr(rep, "docs_processed", -1))
    except Exception as e:
        rec("空库", "空库建索引不抛异常", False, e)
    es.close()
finally:
    for p in (empty, empty+"-wal", empty+"-shm"):
        try: os.remove(p)
        except Exception: pass

# --- 3. LLM 缓存按模型隔离 ---
c = sqlite3.connect(str(s.db_path))
cols = [r[1] for r in c.execute("PRAGMA table_info(llm_cache)").fetchall()]
rec("缓存", "llm_cache 有模型字段", "model" in cols, cols)
n_models = c.execute("select count(distinct model) from llm_cache").fetchone()[0]
rec("缓存", "缓存按模型区分", n_models >= 1, "涉及 %d 个模型" % n_models)
c.close()

# --- 4. 题录级文献（无全文）的溯源行为 ---
st2 = Store(s.db_path); tr2 = QuoteTracer(st2, s)
c = sqlite3.connect(str(s.db_path))
row = c.execute("select d.id, d.title from documents d where d.has_fulltext=0 limit 1").fetchone()
if row:
    rr, rp = tr2.trace(row[1][:120], allow_online=False)
    rec("题录级", "仅题录文献的标题检索不报错", isinstance(rr.level, str), "level=%s conf=%.3f" % (rr.level, rr.confidence))
c.close(); st2.close()

fails = [r for r in RES if not r["ok"]]
print("\n== 第二轮C：%d 项，失败 %d ==" % (len(RES), len(fails)))
for f in fails: print("  FAIL:", f["cat"], f["name"], f["detail"][:130])
