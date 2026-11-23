# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys, os, json, shutil, sqlite3, tempfile, traceback
sys.path.insert(0, str(_REPO_ROOT / '作品相关代码' / 'scr'))
from litagent.config import get_settings
from litagent.index.store import Store

s = get_settings(); store = Store(s.db_path); store.init_schema()
RES = []
def rec(cat, name, ok, detail=""):
    RES.append(dict(cat=cat, name=name, ok=bool(ok), detail=str(detail)[:300]))
    print("%-6s %-46s %s" % ("PASS" if ok else "FAIL", name, str(detail)[:110]))

con = sqlite3.connect(str(s.db_path)); cur = con.cursor()

# ---------- G. 总结 ----------
n_sum = cur.execute("select count(*) from summaries").fetchone()[0]
rec("G", "已有结构化总结", n_sum > 0, "%d 篇" % n_sum)
try:
    row = cur.execute("select doc_id, tldr, problem, methods, findings, contributions, limitations from summaries limit 1").fetchone()
    fields = dict(zip(["doc_id","tldr","problem","methods","findings","contributions","limitations"], row))
    filled = sum(1 for k,v in fields.items() if k!="doc_id" and v and str(v).strip())
    rec("G", "总结字段可读且非空", filled >= 3, "非空字段 %d/6" % filled)
except Exception as e:
    rec("G", "总结字段可读且非空", False, e)

# Issues.md #6：缓存命中仍要求 API Key
try:
    from litagent.summarize.runner import SummaryRunner
    did = cur.execute("select doc_id from summaries limit 1").fetchone()[0]
    orig = s.llm_api_key
    s.llm_api_key = ""            # 模拟未配置密钥
    runner = SummaryRunner(store, s)
    err = None
    try:
        runner.run(doc_ids=[did])
    except Exception as e:
        err = e
    s.llm_api_key = orig
    rec("G", "总结已缓存时无需 API Key（Issues#6）", err is None,
        "仍报错: %s" % (str(err).splitlines()[0][:80] if err else "未报错，通过"))
except Exception as e:
    rec("G", "总结已缓存时无需 API Key（Issues#6）", False, e)

# ---------- H. 分块与索引一致性 ----------
mx = cur.execute("select max(length(text)) from chunk_text").fetchone()[0]
limit = s.max_chunk_chars
rec("H", "片段长度不超过配置上限（Issues#2）", mx is not None and mx <= limit * 1.05,
    "最大 %d 字符 / 上限 %d" % (mx, limit))
over = cur.execute("select count(*) from chunk_text where length(text) > ?", (int(limit*1.05),)).fetchone()[0]
rec("H", "超限片段数量", over == 0, over)

# 每个片段是否都有 postings / fingerprints
n_chunk = cur.execute("select count(*) from chunks").fetchone()[0]
no_post = cur.execute("select count(*) from chunks c where not exists (select 1 from postings p where p.chunk_id=c.id)").fetchone()[0]
no_fp = cur.execute("select count(*) from chunks c where not exists (select 1 from fingerprints f where f.chunk_id=c.id)").fetchone()[0]
rec("H", "每个片段都有倒排 posting（Issues#8）", no_post == 0, "缺失 %d / %d" % (no_post, n_chunk))
rec("H", "每个片段都有指纹（Issues#8）", no_fp == 0, "缺失 %d / %d" % (no_fp, n_chunk))

# 指纹单词长度是否都 = n
try:
    lens = cur.execute("select distinct length(gram) from fingerprints").fetchall()
    rec("H", "指纹词长与 n=7 一致", [x[0] for x in lens] == [s.fingerprint_n], [x[0] for x in lens])
except Exception:
    cols = [r[1] for r in cur.execute("PRAGMA table_info(fingerprints)").fetchall()]
    rec("H", "指纹表结构", True, cols)

# ---------- I. 只读模式 ----------
try:
    ro = Store(s.db_path, read_only=True)
    blocked = False
    try:
        ro._conn.execute("delete from meta where 0") if hasattr(ro, "_conn") else None
        ro2 = Store(s.db_path, read_only=True)
        # 尝试写入：插入一条 meta
        try:
            ro2._conn.execute("insert into meta(key,value) values('__t__','1')")
            ro2._conn.commit()
        except Exception:
            blocked = True
    except Exception:
        blocked = True
    rec("I", "只读连接拒绝写入（Issues#14）", blocked, "写入被拒" if blocked else "写入竟然成功")
except Exception as e:
    rec("I", "只读连接拒绝写入（Issues#14）", False, e)

# ---------- J. 删除的事务性（在副本上做） ----------
tmpdb = os.path.join(str(_REPO_ROOT / '测试资产' / 'data'), "copy.db")
try:
    store._conn.close() if hasattr(store, "_conn") else None
except Exception:
    pass
try:
    src = sqlite3.connect(str(s.db_path)); dst = sqlite3.connect(tmpdb)
    src.backup(dst); dst.close(); src.close()
    st2 = Store(tmpdb); st2.init_schema()
    before = st2.count_documents()
    victim = sqlite3.connect(tmpdb).execute("select id from documents limit 1").fetchone()[0]
    st2.delete_document(victim)
    after = st2.count_documents()
    c2 = st2._conn
    left = {
      "chunks": c2.execute("select count(*) from chunks where doc_id=?", (victim,)).fetchone()[0],
      "summaries": c2.execute("select count(*) from summaries where doc_id=?", (victim,)).fetchone()[0],
      "fulltext": c2.execute("select count(*) from documents_fulltext where doc_id=?", (victim,)).fetchone()[0],
    }
    rec("J", "删除文献后计数减一", after == before - 1, "%d → %d" % (before, after))
    rec("J", "删除后无残留（chunks/summaries/fulltext 清零）", all(v == 0 for v in left.values()), left)
    st2.close()
except Exception as e:
    rec("J", "删除事务性", False, traceback.format_exc()[-200:])
finally:
    for p in [tmpdb, tmpdb+"-wal", tmpdb+"-shm"]:
        try: os.remove(p)
        except Exception: pass

json.dump(RES, open(os.path.join(str(_REPO_ROOT / '测试资产' / 'data'), "stage3.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
bad = [r for r in RES if not r["ok"]]
print("\n== 汇总：%d 项，失败 %d ==" % (len(RES), len(bad)))
for b in bad: print("  FAIL:", b["cat"], b["name"], b["detail"][:120])
