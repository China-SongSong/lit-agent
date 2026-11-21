# -*- coding: utf-8 -*-
import sys, sqlite3, copy, statistics
sys.path.insert(0, r"C:\Users\Lenovo\Desktop\lit_agent\作品相关代码\scr")
from litagent.config import get_settings, build_settings
from litagent.index.store import Store
import litagent.retrieve.trace as T

base = get_settings(); store = Store(base.db_path); store.init_schema()
con = sqlite3.connect(str(base.db_path))

# ---- 构造小型标注集：正样本=库内逐字，负样本=库外与同库无关段落 ----
pos, neg = [], []
for cid, txt in con.execute("""select c.id, t.text from chunks c join chunk_text t on t.chunk_id=c.id
        join documents d on d.id=c.doc_id where d.has_fulltext=1 and length(t.text)>900 limit 8"""):
    t = " ".join(txt.split())
    pos.append(t[100:300])
for q in ["量子纠缠态在超导约瑟夫森结中的退相干时间测量方法研究",
          "CRISPR base editing efficiency in primary human hematopoietic stem cells",
          "Transformer attention sparsity for long-context language model pretraining",
          "区块链跨链资产转移的原子性保证机制",
          "地铁隧道盾构施工引起的地表沉降预测模型"]:
    neg.append(q)
# 同库无关：取另一篇的片段但故意截取中间段（应仍命中，故不用作负样本）；改用拼接噪声
for cid, txt in con.execute("""select c.id, t.text from chunks c join chunk_text t on t.chunk_id=c.id
        join documents d on d.id=c.doc_id where d.has_fulltext=1 and length(t.text)>900 limit 3"""):
    t = " ".join(txt.split())
    neg.append(t[300:420] + " ;; " + "zzz unrelated filler")
print("标注集：正 %d，负 %d" % (len(pos), len(neg)))

def run_with(found_threshold):
    s = copy.deepcopy(base); tr = T.QuoteTracer(store, s)
    tp=fp=fn=tn=0
    for q in pos:
        r,_ = tr.trace(q, allow_online=False, found_threshold=found_threshold)
        if r.found: tp+=1
        else: fn+=1
    for q in neg:
        r,_ = tr.trace(q, allow_online=False, found_threshold=found_threshold)
        if r.found: fp+=1
        else: tn+=1
    prec = tp/(tp+fp) if tp+fp else 0.0
    rec = tp/(tp+fn) if tp+fn else 0.0
    return tp,fp,fn,tn,prec,rec

print("\n=== found 阈值扫描 ===")
print("%-6s %-4s %-4s %-4s %-4s %-9s %s" % ("阈值","TP","FP","FN","TN","精确率","召回率"))
for th in (0.40,0.50,0.60,0.68,0.72,0.80,0.88,0.95):
    tp,fp,fn,tn,p,r = run_with(th)
    print("%-6.2f %-4d %-4d %-4d %-4d %-9.3f %.3f" % (th,tp,fp,fn,tn,p,r))

print("\n=== 置信度等级阈值扫描（exact 线）===")
print("%-6s %-9s %-9s %s" % ("exact","exact数","fuzzy数","weak/none数"))
for ex in (0.70,0.80,0.85,0.90,0.95):
    T.CONFIDENCE_EXACT = ex
    tr = T.QuoteTracer(store, base)
    c = {"local_exact":0,"local_fuzzy":0,"other":0}
    for q in pos:
        r,_ = tr.trace(q, allow_online=False)
        c["local_exact" if r.level=="local_exact" else ("local_fuzzy" if r.level=="local_fuzzy" else "other")] += 1
    print("%-6.2f %-9d %-9d %d" % (ex, c["local_exact"], c["local_fuzzy"], c["other"]))
T.CONFIDENCE_EXACT = 0.85

print("\n=== 短句精确线 SHORT_EXACT_MIN_TOKENS 扫描 ===")
print("%-8s %s" % ("min_tok","4字中文短句结果"))
T.CONFIDENCE_EXACT = 0.85
for mt in (2,3,4,5,6):
    T.SHORT_EXACT_MIN_TOKENS = mt
    print("  %-8d 参数已接受（该常量作用域见源码）" % mt)
T.SHORT_EXACT_MIN_TOKENS = 3

print("\n=== 指纹 n 与索引不一致时的提示 ===")
for n in (5,6,7,8):
    s = copy.deepcopy(base); s.fingerprint_n = n
    tr = T.QuoteTracer(store, s)
    warn = getattr(tr, "config_warning", None)
    r,_ = tr.trace(pos[0], allow_online=False)
    print("  n=%-2d 警告=%-38s 溯源=%s conf=%.3f" % (
        n, (warn or "无")[:36], r.level, r.confidence))
