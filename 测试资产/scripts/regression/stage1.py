# -*- coding: utf-8 -*-
import sys, os, json, sqlite3, importlib, pkgutil, traceback
SCR = r"C:\Users\Lenovo\Desktop\lit_agent\作品相关代码\scr"
sys.path.insert(0, SCR)
RES = []
def rec(cat, name, ok, detail=""):
    RES.append(dict(cat=cat, name=name, ok=bool(ok), detail=str(detail)[:300]))
    print("%-6s %-42s %s" % ("PASS" if ok else "FAIL", name, str(detail)[:120]))

# ---------- B. 模块导入 ----------
import litagent
fails = []
count = 0
for m in pkgutil.walk_packages(litagent.__path__, "litagent."):
    count += 1
    try:
        importlib.import_module(m.name)
    except Exception as e:
        fails.append("%s: %s" % (m.name, e))
rec("B", "模块导入（%d 个）" % count, not fails, "; ".join(fails[:3]))
for extra in ["app", "cli"]:
    try:
        spec = importlib.util.find_spec(extra)
        rec("B", "入口可解析 %s.py" % extra, spec is not None)
    except Exception as e:
        rec("B", "入口可解析 %s.py" % extra, False, e)

# ---------- C. 数据库完整性 ----------
from litagent.config import get_settings
from litagent.index.store import Store
s = get_settings()
db = str(s.db_path)
rec("C", "数据库存在", os.path.exists(db), db)
con = sqlite3.connect(db); cur = con.cursor()
cur.execute("select name from sqlite_master where type='table' order by name")
tables = [r[0] for r in cur.fetchall()]
rec("C", "数据表数量", len(tables) >= 8, ",".join(tables))
integrity = cur.execute("pragma integrity_check").fetchone()[0]
rec("C", "PRAGMA integrity_check", integrity == "ok", integrity)

store = Store(s.db_path)
st = store.stats()
rec("C", "stats() 可调用", isinstance(st, dict), json.dumps(st, ensure_ascii=False)[:200])

# 计数一致性
n_doc = store.count_documents()
n_doc_ft = store.count_documents(only_with_fulltext=True)
n_chunk = store.count_chunks()
n_post = store.count_postings()
n_fp = store.count_fingerprints()
n_sum = store.count_summaries()
rec("C", "文献数 > 0", n_doc > 0, n_doc)
rec("C", "片段数 > 0", n_chunk > 0, n_chunk)
rec("C", "含全文文献 <= 文献总数", n_doc_ft <= n_doc, "%d / %d" % (n_doc_ft, n_doc))
rec("C", "总结数 <= 文献数", n_sum <= n_doc, "%d / %d" % (n_sum, n_doc))
rec("C", "postings > 0", n_post > 0, n_post)
rec("C", "fingerprints > 0", n_fp > 0, n_fp)

# 孤儿数据
def q1(sql):
    return cur.execute(sql).fetchone()[0]
orph = {
  "chunk 无对应文献": q1("select count(*) from chunks c left join documents d on d.id=c.doc_id where d.id is null"),
  "chunk_text 无对应片段": q1("select count(*) from chunk_text t left join chunks c on c.id=t.chunk_id where c.id is null"),
  "postings 指空片段": q1("select count(*) from postings p left join chunks c on c.id=p.chunk_id where c.id is null"),
  "fingerprints 指空片段": q1("select count(*) from fingerprints f left join chunks c on c.id=f.chunk_id where c.id is null"),
  "summaries 指空文献": q1("select count(*) from summaries s left join documents d on d.id=s.doc_id where d.id is null"),
}
for k, v in orph.items():
    rec("C", "孤儿检查：" + k, v == 0, v)

# 空文本片段
empty = q1("select count(*) from chunk_text where trim(text) = ''")
rec("C", "空文本片段", empty == 0, empty)

# 全文一致性：has_fulltext 与 documents_fulltext 表
try:
    n_ft_tbl = q1("select count(*) from documents_fulltext")
    rec("C", "documents_fulltext 行数 == 含全文文献数", n_ft_tbl == n_doc_ft, "%d vs %d" % (n_ft_tbl, n_doc_ft))
except Exception as e:
    rec("C", "documents_fulltext 表", False, e)

# 指纹参数一致性
try:
    from litagent.index.phrase import PhraseIndex
    ph = PhraseIndex(store)
    rec("C", "指纹 n 与配置一致", ph.n_from_index in (None, s.fingerprint_n), "index=%s cfg=%s" % (ph.n_from_index, s.fingerprint_n))
except Exception as e:
    rec("C", "指纹参数读取", False, e)

json.dump(RES, open(os.path.join(r"C:/Users/Lenovo/Desktop/lit_agent/测试资产/data", "stage1.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
bad = [r for r in RES if not r["ok"]]
print("\n== 汇总：%d 项，失败 %d ==" % (len(RES), len(bad)))
for b in bad:
    print("  FAIL:", b["cat"], b["name"], b["detail"][:100])
