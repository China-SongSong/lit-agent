# -*- coding: utf-8 -*-
"""lit-agent 黑盒溯源测试（可复现）。

这是一个独立、自包含的黑盒测试脚本，不依赖任何其它测试基建。
它从当前生产库中抽取真实全文片段作为正例，并构造负例、改写、跨语言等
对抗性用例，端到端验证系统的核心契约——"拒绝把相似当成来源"。

运行（任意工作目录均可）：

    python blackbox_trace.py            # 自动向上查找 scr 目录
    python blackbox_trace.py --scr <scr绝对路径>   # 或显式指定 scr 目录
    python blackbox_trace.py --no-online           # 联网用例也强制离线

退出码：0 表示全部通过，1 表示存在失败用例。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path


def _find_scr(start: Path) -> Path:
    """从当前脚本所在位置向上查找 '作品相关代码/scr' 目录。"""
    cur = start
    for _ in range(6):  # 最多向上 6 层
        for cand in (
            cur / "作品相关代码" / "scr",
            cur / "scr",
        ):
            if (cand / "litagent").is_dir():
                return cand.resolve()
        if cur.parent == cur:
            break
        cur = cur.parent
    raise FileNotFoundError("未找到 scr 目录，请用 --scr 显式指定")


def main() -> int:
    ap = argparse.ArgumentParser(description="lit-agent 黑盒溯源测试")
    ap.add_argument("--scr", help="scr 目录的绝对路径（默认自动查找）")
    ap.add_argument("--no-online", action="store_true", help="联网用例也强制离线")
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    scr = Path(args.scr).resolve() if args.scr else _find_scr(here)
    sys.path.insert(0, str(scr))

    from litagent.config import get_settings
    from litagent.index.store import Store
    from litagent.retrieve.trace import QuoteTracer

    s = get_settings()
    store = Store(s.db_path)
    store.init_schema()
    tr = QuoteTracer(store, s)
    con = sqlite3.connect(str(s.db_path))

    RES = []

    def rec(cat, name, ok, detail=""):
        RES.append(dict(cat=cat, name=name, ok=bool(ok), detail=str(detail)[:200]))
        print("%-6s [%s] %-38s %s" % ("PASS" if ok else "FAIL", cat, name, str(detail)[:90]))

    # ---- 一、从全文文献抽取真实原文片段作为正例 ----
    rec("准备", "生产库连接", True, "文献 %d 篇" % con.execute("select count(*) from documents").fetchone()[0])
    rows = con.execute(
        """select d.id, d.title, t.text from chunks c
           join chunk_text t on t.chunk_id = c.id
           join documents d on d.id = c.doc_id
           where d.has_fulltext = 1 and length(t.text) > 600
           order by d.id, c.id"""
    ).fetchall()
    # 每篇全文文献取一段，去重
    seen = {}
    for did, title, text in rows:
        if did in seen:
            continue
        norm = " ".join(text.split())
        if len(norm) < 220:
            continue
        seen[did] = (title, norm[120:320])
    quotes = [dict(doc_id=k, title=v[0], quote=v[1]) for k, v in seen.items()]
    rec("准备", "抽取真实正例片段", len(quotes) > 0, "%d 篇全文文献" % len(quotes))

    # ---- 二、正例溯源 ----
    hit = 0
    for i, q in enumerate(quotes):
        r, _ = tr.trace(q["quote"], allow_online=False)
        ok = r.found and (r.hit is not None) and (r.hit.doc_id == q["doc_id"])
        hit += int(ok)
        rec("正例", "全文片段 %d" % i, ok, q["title"][:40])
    rec("正例", "准确率", hit == len(quotes) and len(quotes) > 0, "%d/%d" % (hit, len(quotes)))

    # ---- 三、负例与边界用例 ----
    neg_cases = [
        ("完全无关中文", "量子纠缠态在超导约瑟夫森结中的退相干时间测量方法研究"),
        ("完全无关英文", "The effect of coffee consumption on the mating behavior of emperor penguins in Antarctica"),
        ("空输入", ""),
        ("纯标点", "。。。！！？？"),
        ("超短词", "电池"),
        ("乱码", "asdfghjkl qwertyuiop zxcvbnm"),
        ("仅数字", "1234567890"),
    ]
    for name, txt in neg_cases:
        r, rp = tr.trace(txt, allow_online=False)
        ok = (not r.found) and r.confidence <= 1e-9
        rec("负例", name, ok, "level=%s conf=%.3f" % (r.level, r.confidence))

    # ---- 四、改写 / 转述（验证核心卖点） ----
    if quotes:
        orig = quotes[0]["quote"]
        # 轻度改写：换一个实义词，应仍能命中（连续词元对齐达标）
        light = orig.replace("analyzes", "examines", 1)
        if light == orig:  # 该片段若不含该词则换一个词
            light = orig.replace("study", "research", 1)
        r1, _ = tr.trace(light, allow_online=False)
        rec("改写", "轻度换词仍命中", r1.found and r1.level in ("local_exact", "local_fuzzy"),
            "level=%s conf=%.3f" % (r1.level, r1.confidence))

        # 重度转述：换句式，语义相似但不逐字，应拒绝确认出处
        heavy = ("This research examines how the full text of articles from the "
                 "PubMed Central Open Access collection can be used for evaluation purposes.")
        r2, _ = tr.trace(heavy, allow_online=False)
        rec("改写", "重度转述被拒绝", (not r2.found) and r2.level == "local_semantic",
            "level=%s conf=%.3f" % (r2.level, r2.confidence))

        # 替换多个关键实体：破坏足够多的连续词元对齐后，系统不应再确认出处。
        # 只换一个词时大段连续文本仍精确匹配，系统判定命中是合理行为；
        # 这里替换原文中出现频率最高的多个实义词，验证对齐被破坏后的拒绝能力。
        import re as _re
        words = [w for w in _re.findall(r"[A-Za-z]{5,}", orig)]
        if len(words) >= 3:
            to_replace = words[: max(2, len(words) // 3)]  # 替换前 1/3 的词
            mid = orig
            for w in to_replace:
                mid = mid.replace(w, "ZZZ", 1)
            r3, _ = tr.trace(mid, allow_online=False)
            rec("改写", "替换多个实体被拒绝", (not r3.found),
                "level=%s conf=%.3f 替换%d词" % (r3.level, r3.confidence, len(to_replace)))
        else:
            rec("改写", "替换多个实体被拒绝", True, "片段英文词过少，跳过")

    # ---- 五、跨语言 ----
    zh = "本研究分析了来自 PubMed Central 开放获取数据集的全文学术论文，以评估其影响。"
    rz, _ = tr.trace(zh, allow_online=False)
    rec("跨语言", "中文描述英文文献不确认出处", (not rz.found),
        "level=%s conf=%.3f" % (rz.level, rz.confidence))

    # ---- 六、联网兜底（可选） ----
    if not args.no_online:
        # 本地未命中的英文查询，联网应返回候选并诚实标注"不在本地库"
        rw, _ = tr.trace("graph neural network recommendation system cold start meta learning",
                         allow_online=True)
        online_cands = list(getattr(rw, "online_candidates", []) or [])
        rec("联网", "本地未命中时联网返回候选", len(online_cands) > 0,
            "候选 %d 条" % len(online_cands))
        rec("联网", "联网候选不误报为出处", (not rw.found),
            "found=%s" % rw.found)
    else:
        rec("联网", "跳过联网用例", True, "--no-online 已指定")

    con.close()
    try:
        tr.close()
    except Exception:
        pass
    try:
        store.close()
    except Exception:
        pass

    # ---- 汇总 ----
    fails = [x for x in RES if not x["ok"]]
    print()
    print("== 黑盒溯源测试：%d 项，失败 %d ==" % (len(RES), len(fails)))
    for f in fails:
        print("  FAIL:", f["cat"], f["name"], f["detail"][:100])

    # 输出 JSON 供 CI 或归档
    out = Path(__file__).resolve().parents[1] / "data" / "blackbox_result.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(RES, ensure_ascii=False, indent=1), encoding="utf-8")
    print("结果已写入：%s" % out)

    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
