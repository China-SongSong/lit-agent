# -*- coding: utf-8 -*-

from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[3]

import json, re, os, random
FX = str(_REPO_ROOT / '作品相关代码' / 'scr' / 'tests' / 'fixtures')
corpus = [json.loads(l) for l in open(os.path.join(FX,"calibration_corpus_v2.jsonl"), encoding="utf-8") if l.strip()]
old = [json.loads(l) for l in open(os.path.join(FX,"calibration_queries_v2.jsonl"), encoding="utf-8") if l.strip()]
print("原有查询 %d 条，语料 %d 篇" % (len(old), len(corpus)))

def sents(t):
    parts = re.split(r"(?<=[。！？])|(?<=[.!?])\s+", t)
    return [p.strip() for p in parts if len(p.strip()) >= 30]

new = []
def add(qid, query, qtype, should, docs, lang, note=""):
    new.append(dict(id=qid, query=query, query_type=qtype, split="test",
                    should_find=should, expected_doc_ids=docs, language=lang,
                    **({"note":note} if note else {})))

for d in corpus:
    did = d["id"]; ft = (d.get("fulltext") or "").strip(); lang = d.get("language","zh")
    if len(ft) < 100:
        # 无全文：标题检索不应命中正文
        add("add-%s-title" % did.split(":")[-1], d["title"], "title_only", False, [], lang,
            "仅题录，标题不构成正文证据")
        continue
    ss = sents(ft)
    if not ss: continue
    tag = did.split(":")[-1]
    # 1 逐字整句
    add("add-%s-exact" % tag, ss[0], "exact", True, [did], lang)
    # 2 句尾片段
    add("add-%s-tail" % tag, ss[-1][-60:], "tail", True, [did], lang)
    # 3 短片段
    if len(ss[0]) > 45:
        add("add-%s-short" % tag, ss[0][:40], "short", True, [did], lang)
    # 4 标点变体（句号改逗号、去空格）
    if len(ss) > 1:
        v = ss[1].replace("。", ",").replace(".", ",").strip()
        add("add-%s-punct" % tag, v, "punctuation", True, [did], lang)
    # 5 跨句拼接
    if len(ss) > 2:
        add("add-%s-span" % tag, ss[0] + ss[2], "span", True, [did], lang)

# 硬负样本：同主题改写，不应归属到任何文献
hard = [
 ("add-hard-gnn", "在图神经网络推荐中，冷启动用户往往交互极少，本文用元学习初始化来提升低资源场景的表现。", "zh"),
 ("add-hard-citation", "引文溯源需要把原文证据与语义相似分开处理，外部结果在入库前都只是待核对候选。", "zh"),
 ("add-hard-biblio", "文献计量网络的中心性会随统计窗口变化，因此比较不同研究时必须先对齐窗口口径。", "zh"),
 ("add-hard-semantic", "Semantic similarity can surface related passages, yet it cannot establish where a sentence originally came from.", "en"),
]
for qid, q, lang in hard:
    add(qid, q, "hard_negative", False, [], lang, "同主题改写，无逐字证据")

# 纯负样本：域外
outer = [
 ("add-neg-quantum", "量子纠缠态在超导约瑟夫森结中的退相干时间测量方法研究", "zh"),
 ("add-neg-crispr", "CRISPR base editing efficiency in primary human hematopoietic stem cells", "en"),
 ("add-neg-transformer", "Transformer attention sparsity for long-context language model pretraining", "en"),
 ("add-neg-metro", "地铁隧道盾构施工引起的地表沉降预测模型", "zh"),
]
for qid, q, lang in outer:
    add(qid, q, "negative", False, [], lang, "库外无关文本")

# 校验：正样本必须能在对应文献全文中找到
bad = []
ftmap = {d["id"]: (d.get("fulltext") or "") for d in corpus}
for c in new:
    if c["should_find"]:
        target = ftmap.get(c["expected_doc_ids"][0], "")
        probe = c["query"][:30]
        if probe not in target:
            bad.append(c["id"])
print("新增 %d 条；正样本校验未通过 %d 条 %s" % (len(new), len(bad), bad[:5]))

out = old + new
dst = os.path.join(FX, "calibration_queries_v3.jsonl")
with open(dst, "w", encoding="utf-8") as f:
    for c in out:
        f.write(json.dumps(c, ensure_ascii=False) + "\n")
print("写出:", dst, "共 %d 条（旧 %d + 新 %d）" % (len(out), len(old), len(new)))
from collections import Counter
print("类型分布:", dict(Counter(c["query_type"] for c in out)))
print("拆分分布:", dict(Counter(c["split"] for c in out)))
print("正负分布:", dict(Counter(c["should_find"] for c in out)))
