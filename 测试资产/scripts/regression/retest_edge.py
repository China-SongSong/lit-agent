# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import sys, os, sqlite3, traceback
sys.path.insert(0, str(_REPO_ROOT / '作品相关代码' / 'scr'))
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.index.builder import IndexBuilder
from litagent.clean.chunker import chunk_text
from litagent.schema import Document, Author
from litagent.retrieve.trace import QuoteTracer

RES = []
def rec(cat, name, ok, detail=""):
    RES.append(dict(cat=cat, name=name, ok=bool(ok), detail=str(detail)[:260]))
    print("%-6s [%s] %-40s %s" % ("PASS" if ok else "FAIL", cat, name, str(detail)[:110]))

s = get_settings()

# ---------- 中文短句（回归：4 字） ----------
TMP = os.path.join(str(_REPO_ROOT / '测试资产' / 'data'), "zh2.db")
for p in (TMP, TMP+"-wal", TMP+"-shm"):
    try: os.remove(p)
    except Exception: pass
store = Store(TMP); store.init_schema()
text = (
"文献引文溯源系统的设计目标。\n"
"本文提出一套面向论文写作的文献引文溯源系统，用于在本地语料库中定位一段文字的原始出处。"
"该系统以精确指纹为主判据，以关键词检索为旁证，并输出符合国家标准的参考文献格式。\n"
"指纹匹配的基本原理。\n"
"系统先把正文切分为语义片段，再对每个片段抽取固定长度的字符窗口，把窗口映射为整数指纹并建立倒排表。"
"查询时对输入文本做同样的处理，统计命中窗口占全部窗口的比例，据此换算置信度与等级。\n"
"容错设计。\n"
"对于短于指纹长度的中文短句，系统改用退化的匹配策略，保证三到六个汉字的片段仍然可以被检索到，"
"从而避免中文用户粘贴短句时出现本不该出现的未命中。\n"
"局限与展望。\n"
"当前语料规模有限，指标不宜外推。后续将引入更大规模语料与更严格的人工标注，以校准置信度。\n"
)
doc = Document(id="zh:test-0001", title="文献引文溯源系统设计说明", language="zh",
               source="test", venue="测试期刊", year=2026, authors=[Author(family="张", given="三")])
store.upsert_document(doc); store.set_fulltext(doc.id, text)
store.replace_chunks(doc.id, chunk_text(doc.id, text, max_chars=s.max_chunk_chars,
                                        overlap_chars=s.chunk_overlap_chars, min_chars=50))
IndexBuilder(store, s).build_lexical(doc_ids=[doc.id])
tr = QuoteTracer(store, s)
for name, q, expect in [("六字", "文献引文溯源系统", True), ("五字", "指纹匹配的", True), ("四字", "容错设计", True)]:
    r, rp = tr.trace(q, allow_online=False)
    rec("中文短句", "%s片段可判定为找到（回归）" % name, bool(r.found) == expect,
        "level=%s conf=%.3f found=%s cand=%d" % (r.level, r.confidence, r.found, rp.phrase_candidates))
for p in (TMP, TMP+"-wal", TMP+"-shm"):
    try: os.remove(p)
    except Exception: pass

# ---------- 只读 ----------
try:
    ro = Store(s.db_path, read_only=True)
    blocked = False
    try:
        ro.connect().execute("insert into meta(key,value) values('__t__','1')")
        ro.connect().commit()
    except Exception:
        blocked = True
    rec("边界", "只读连接拒绝写入（回归）", blocked)
except Exception as e:
    rec("边界", "只读连接拒绝写入（回归）", False, e)

# ---------- 删除 ----------
tmp = os.path.join(str(_REPO_ROOT / '测试资产' / 'data'), "copy2.db")
for p in (tmp, tmp+"-wal", tmp+"-shm"):
    try: os.remove(p)
    except Exception: pass
try:
    src = sqlite3.connect(str(s.db_path)); dst = sqlite3.connect(tmp); src.backup(dst); dst.close(); src.close()
    st = Store(tmp); st.init_schema()
    v = sqlite3.connect(tmp).execute("select id from documents limit 1").fetchone()[0]
    before = st.count_documents(); st.delete_document(v); st.close()
    c = sqlite3.connect(tmp)
    after = c.execute("select count(*) from documents").fetchone()[0]
    left = sum(c.execute(q, (v,)).fetchone()[0] for q in [
        "select count(*) from chunks where doc_id=?",
        "select count(*) from summaries where doc_id=?",
        "select count(*) from documents_fulltext where doc_id=?",
        "select count(*) from chunks c where c.doc_id=? and not exists(select 1 from chunk_text t where t.chunk_id=c.id)"])
    orphan = c.execute("select count(*) from postings p left join chunks k on k.id=p.chunk_id where k.id is null").fetchone()[0]
    c.close()
    rec("边界", "删除后计数减一", after == before - 1, "%d→%d" % (before, after))
    rec("边界", "删除后无残留", left == 0 and orphan == 0, "left=%d orphan=%d" % (left, orphan))
except Exception:
    rec("边界", "删除完整性", False, traceback.format_exc()[-150:])
finally:
    for p in (tmp, tmp+"-wal", tmp+"-shm"):
        try: os.remove(p)
        except Exception: pass

fails = [r for r in RES if not r["ok"]]
print("\n== 边界：%d 项，失败 %d ==" % (len(RES), len(fails)))
for f in fails: print("  FAIL:", f["cat"], f["name"], f["detail"][:120])
