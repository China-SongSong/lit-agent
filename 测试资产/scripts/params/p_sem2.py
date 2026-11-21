# -*- coding: utf-8 -*-
import sys, sqlite3, copy, os
sys.path.insert(0, r"C:\Users\Lenovo\Desktop\lit_agent\作品相关代码\scr")
from litagent.config import get_settings
from litagent.index.store import Store
import litagent.retrieve.trace as T

base = get_settings(); store = Store(base.db_path); store.init_schema()
con = sqlite3.connect(str(base.db_path))

# 干净的正/负样本
pos = []
for cid, txt in con.execute("""select c.id, t.text from chunks c join chunk_text t on t.chunk_id=c.id
        join documents d on d.id=c.doc_id where d.has_fulltext=1 and length(t.text)>900 limit 8"""):
    t = " ".join(txt.split()); pos.append(t[100:300])
neg = ["量子纠缠态在超导约瑟夫森结中的退相干时间测量方法研究",
       "CRISPR base editing efficiency in primary human hematopoietic stem cells",
       "Transformer attention sparsity for long-context language model pretraining",
       "区块链跨链资产转移的原子性保证机制",
       "地铁隧道盾构施工引起的地表沉降预测模型",
       "sourdough starter fermentation kinetics under varying hydration levels",
       "the phonological loop in working memory revisited",
       "permaculture design principles for temperate smallholdings"]
# 仅题录（无全文）文献的标题：不应被溯源命中正文
row = con.execute("select title from documents where has_fulltext=0 limit 1").fetchone()
print("正样本 %d，负样本 %d" % (len(pos), len(neg)))

def evaluate(th):
    tr = T.QuoteTracer(store, base)
    tp=fp=fn=tn=0
    for q in pos:
        r,_ = tr.trace(q, allow_online=False, found_threshold=th)
        tp += r.found; fn += (not r.found)
    for q in neg:
        r,_ = tr.trace(q, allow_online=False, found_threshold=th)
        fp += r.found; tn += (not r.found)
    return tp,fp,fn,tn

print("\n=== found 阈值扫描（干净负样本）===")
print("%-6s %-4s %-4s %-4s %-4s %-9s %s" % ("阈值","TP","FP","FN","TN","精确率","召回率"))
rows=[]
for th in (0.40,0.50,0.60,0.68,0.72,0.76,0.80,0.85,0.90,0.95):
    tp,fp,fn,tn = evaluate(th)
    p = tp/(tp+fp) if tp+fp else 0.0; r = tp/(tp+fn) if tp+fn else 0.0
    rows.append((th,p,r))
    print("%-6.2f %-4d %-4d %-4d %-4d %-9.3f %.3f" % (th,tp,fp,fn,tn,p,r))

print("\n=== SHORT_EXACT_MIN_TOKENS 对中文短句的作用 ===")
TMP = r"C:/Users/Lenovo/Desktop/lit_agent/测试资产/data\zhp.db"
for p in (TMP,TMP+"-wal",TMP+"-shm"):
    try: os.remove(p)
    except Exception: pass
from litagent.index.builder import IndexBuilder
from litagent.clean.chunker import chunk_text
from litagent.schema import Document, Author
st = Store(TMP); st.init_schema()
text = ("文献引文溯源系统的设计目标。\n本文提出一套面向论文写作的文献引文溯源系统，用于在本地语料库中定位一段文字的原始出处。"
        "该系统以精确指纹为主判据，以关键词检索为旁证，并输出符合国家标准的参考文献格式。\n"
        "容错设计。\n对于短于指纹长度的中文短句，系统改用退化的匹配策略，保证三到六个汉字的片段仍然可以被检索到。\n"
        "局限与展望。\n当前语料规模有限，指标不宜外推。后续将引入更大规模语料与更严格的人工标注。\n")
doc = Document(id="zh:p1", title="文献引文溯源系统设计说明", language="zh", source="test",
               authors=[Author(family="张", given="三")])
st.upsert_document(doc); st.set_fulltext(doc.id, text)
st.replace_chunks(doc.id, chunk_text(doc.id, text, max_chars=1800, overlap_chars=200, min_chars=50))
IndexBuilder(st, base).build_lexical(doc_ids=[doc.id])
print("%-10s %-12s %-9s %s" % ("min_tok","4字短句等级","置信度","找到"))
for mt in (2,3,4,5,6,8):
    T.SHORT_EXACT_MIN_TOKENS = mt
    tr = T.QuoteTracer(st, base)
    r,_ = tr.trace("容错设计", allow_online=False)
    print("%-10d %-12s %-9.4f %s" % (mt, r.level, r.confidence, r.found))
T.SHORT_EXACT_MIN_TOKENS = 3
st.close()
for p in (TMP,TMP+"-wal",TMP+"-shm"):
    try: os.remove(p)
    except Exception: pass
