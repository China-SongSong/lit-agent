# -*- coding: utf-8 -*-
import sys, os, sqlite3, traceback
sys.path.insert(0, r"C:\Users\Lenovo\Desktop\lit_agent\作品相关代码\scr")
from litagent.config import get_settings
from litagent.index.store import Store
from litagent.index.builder import IndexBuilder
from litagent.clean.chunker import chunk_text
from litagent.schema import Document, Author
from litagent.retrieve.trace import QuoteTracer

TMP = os.path.join(r"C:/Users/Lenovo/Desktop/lit_agent/测试资产/data", "zh.db")
for p in (TMP, TMP+"-wal", TMP+"-shm"):
    try: os.remove(p)
    except Exception: pass

s = get_settings(); store = Store(TMP); store.init_schema()
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
               source="test", venue="测试期刊", year=2026,
               authors=[Author(family="张", given="三")])
store.upsert_document(doc)
store.set_fulltext(doc.id, text)
chunks = chunk_text(doc.id, text, max_chars=s.max_chunk_chars, overlap_chars=s.chunk_overlap_chars, min_chars=50)
print("切块数:", len(chunks))
store.replace_chunks(doc.id, chunks)
rep = IndexBuilder(store, s).build_lexical(doc_ids=[doc.id])
print("建索引:", rep.summary() if hasattr(rep, "summary") else rep)

tracer = QuoteTracer(store, s)
tests = [
    ("六字中文短句", "文献引文溯源系统"),
    ("四字中文短句", "容错设计"),
    ("五字中文短句", "指纹匹配的"),
    ("整句中文", "查询时对输入文本做同样的处理，统计命中窗口占全部窗口的比例，据此换算置信度与等级。"),
]
print("\n%-14s %-14s %-6s %-6s %s" % ("用例", "等级", "置信度", "找到", "候选"))
for name, q in tests:
    r, rp = tracer.trace(q, allow_online=False)
    print("%-14s %-14s %-6.3f %-6s cand=%d tokens=%d" % (name, r.level, r.confidence, r.found, rp.phrase_candidates, rp.quote_tokens))

print("\n引文:", tracer._safe_citation(store.get_document(doc.id, with_fulltext=False)))
for p in (TMP, TMP+"-wal", TMP+"-shm"):
    try: os.remove(p)
    except Exception: pass
