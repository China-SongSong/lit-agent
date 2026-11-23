"""生成并运行固定的大规模 lit-agent 基准集。

基准文本是确定性合成语料，用于测试索引、检索、溯源和性能边界，不用于
冒充真实学术资料。语料、查询清单和哈希在运行测评前冻结。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
import tempfile
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from litagent.evaluation.calibration import build_evaluation_store, load_cases
from litagent.retrieve.search import HybridSearcher
from litagent.retrieve.trace import QuoteTracer

BENCHMARK_VERSION="large-v1"
DEFAULT_DOCUMENT_COUNT=10000

TOPICS:Tuple[Tuple[str,Tuple[str,...]],...]=(
    ("artificial intelligence",("reasoning","agents","planning","alignment","knowledge")),
    ("natural language processing",("translation","syntax","semantics","dialogue","corpus")),
    ("computer vision",("segmentation","detection","recognition","geometry","imaging")),
    ("robotics",("motion","control","perception","manipulation","localization")),
    ("database systems",("query","transaction","index","storage","optimization")),
    ("distributed systems",("consensus","replication","latency","partition","scheduling")),
    ("cybersecurity",("authentication","malware","privacy","encryption","vulnerability")),
    ("computer networking",("routing","congestion","protocol","wireless","measurement")),
    ("human computer interaction",("usability","accessibility","interface","interaction","design")),
    ("software engineering",("testing","maintenance","refactoring","requirements","repository")),
    ("information retrieval",("ranking","relevance","query","indexing","evaluation")),
    ("bibliometrics",("citations","journals","authors","collaboration","indicators")),
    ("digital humanities",("archives","text","history","annotation","corpora")),
    ("climate science",("temperature","emissions","adaptation","models","precipitation")),
    ("energy systems",("battery","grid","storage","efficiency","renewables")),
    ("materials science",("alloys","crystals","microstructure","properties","synthesis")),
    ("chemistry",("catalysis","molecules","reaction","spectroscopy","kinetics")),
    ("molecular biology",("proteins","genes","cells","expression","regulation")),
    ("genetics",("variants","heredity","sequencing","population","mutation")),
    ("medicine",("diagnosis","treatment","patients","clinical","therapy")),
    ("epidemiology",("incidence","risk","surveillance","cohort","transmission")),
    ("public health",("prevention","policy","community","screening","services")),
    ("neuroscience",("neurons","brain","cognition","circuits","plasticity")),
    ("psychology",("behavior","memory","emotion","learning","attention")),
    ("economics",("markets","labor","growth","prices","incentives")),
    ("finance",("risk","portfolio","volatility","credit","investment")),
    ("management",("strategy","innovation","organization","teams","performance")),
    ("education",("learning","teachers","students","assessment","curriculum")),
    ("law",("regulation","rights","courts","liability","policy")),
    ("sociology",("inequality","networks","institutions","communities","mobility")),
    ("political science",("elections","governance","institutions","voting","conflict")),
    ("history",("archives","sources","periods","interpretation","evidence")),
    ("archaeology",("sites","artifacts","excavation","chronology","heritage")),
    ("linguistics",("phonology","grammar","pragmatics","language","corpus")),
    ("philosophy",("ethics","epistemology","logic","metaphysics","argument")),
    ("mathematics",("proof","algebra","topology","geometry","analysis")),
    ("statistics",("inference","estimation","variance","sampling","prediction")),
    ("operations research",("optimization","scheduling","routing","allocation","queues")),
    ("civil engineering",("concrete","bridges","structures","transport","construction")),
    ("mechanical engineering",("motors","thermal","turbines","manufacturing","dynamics")),
    ("electrical engineering",("circuits","signals","power","semiconductors","antennas")),
    ("control systems",("stability","feedback","robustness","tracking","estimation")),
    ("environmental science",("ecosystems","pollution","water","biodiversity","conservation")),
    ("agriculture",("crops","soil","yield","irrigation","sustainability")),
    ("food science",("nutrition","safety","processing","quality","storage")),
    ("pharmacology",("drugs","dose","receptors","trials","metabolism")),
    ("astrophysics",("stars","galaxies","telescopes","cosmology","spectra")),
    ("quantum computing",("qubits","gates","error","circuits","algorithms")),
    ("geoscience",("minerals","earthquakes","sediment","geology","survey")),
    ("urban planning",("housing","mobility","zoning","cities","infrastructure")),
)


@dataclass(frozen=True)
class GeneratedCase:
    """固定测评查询。"""

    id:str
    query:str
    query_type:str
    split:str
    should_find:bool
    expected_doc_ids:List[str]
    language:str

    def to_dict(self)->dict:
        return{
            "id":self.id,
            "query":self.query,
            "query_type":self.query_type,
            "split":self.split,
            "should_find":self.should_find,
            "expected_doc_ids":self.expected_doc_ids,
            "language":self.language,
        }


def _sha256(path:Path)->str:
    digest=hashlib.sha256()
    with open(path,"rb") as handle:
        for block in iter(lambda:handle.read(1024*1024),b""):
            digest.update(block)
    return digest.hexdigest()


def _serial_terms(topic:Tuple[str,str],serial:int)->str:
    return "%s-%05d"%(topic,serial)


def _sentence(topic_words:Sequence[str],serial:int,rng:random.Random,index:int)->str:
    a=rng.choice(topic_words)
    b=rng.choice(topic_words)
    c=rng.choice(topic_words)
    return(
        "The benchmark study %s examines %s and %s in scenario %d, "
        "using %s alongside a validated comparison baseline."
        %(_serial_terms("study",serial),a,b,index,c)
    )


def _make_fulltext(topic_label:str,topic_words:Sequence[str],serial:int,rng:random.Random)->Tuple[str,str]:
    anchor=(
        "For benchmark trace %s, the controlled result shows that %s improves %s "
        "by %d percent when tested against %s in scenario %d."
        %(
            _serial_terms("record",serial),
            rng.choice(topic_words),
            rng.choice(("accuracy","coverage","efficiency","stability","relevance")),
            rng.randint(4,37),
            rng.choice(topic_words),
            serial%97,
        )
    )
    sections=("Abstract","Introduction","Related Work","Method","Experiments",
              "Results","Discussion","Conclusion")
    paragraphs=[]
    for section_index,section in enumerate(sections):
        body=" ".join(
            _sentence(topic_words,serial,rng,section_index*4+offset)
            for offset in range(4)
        )
        paragraphs.append("%s\n%s"%(section,body))
    paragraphs[4]+="\n"+anchor
    fulltext=("\n\n".join(paragraphs)).replace("study-","research-")
    return fulltext,anchor


def _make_abstract(topic_label:str,topic_words:Sequence[str],serial:int,rng:random.Random)->str:
    return(
        "This synthetic benchmark abstract %s reviews %s, %s, and %s. "
        "It reports a controlled comparison for topic %s and records limitations."
        %(
            _serial_terms("abstract",serial),
            rng.choice(topic_words),
            rng.choice(topic_words),
            rng.choice(topic_words),
            topic_label,
        )
    )


def generate_benchmark(
    output_dir:Path,
    *,
    document_count:int=DEFAULT_DOCUMENT_COUNT,
    seed:int=20261118,
)->dict:
    """生成固定语料和查询，写入后不应再修改。"""
    output_dir.mkdir(parents=True,exist_ok=False)
    corpus_path=output_dir/"corpus.jsonl"
    queries_path=output_dir/"queries.jsonl"
    rng=random.Random(seed)
    fulltext_anchors:List[Tuple[str,str,str]]=[]
    topic_fulltext_ids:Dict[str,List[str]]=defaultdict(list)
    query_rows:List[GeneratedCase]=[]

    with open(corpus_path,"w",encoding="utf-8") as handle:
        for index in range(document_count):
            topic_index=index%len(TOPICS)
            topic_label,topic_words=TOPICS[topic_index]
            per_topic=index//len(TOPICS)
            serial=index+1
            doc_doi="10.9999/large.%s.%05d"%(re.sub(r"[^a-z0-9]+","-",topic_label),serial)
            doc_id="doi:"+doc_doi
            title="%s Benchmark Study %05d"%(topic_label.title(),serial)
            authors=[{"family":"Bench","given":"Author %05d"%serial,"raw":"Author Bench"}]
            row={
                "id":doc_id,
                "doi":doc_doi,
                "title":title,
                "authors":authors,
                "year":2000+(serial%26),
                "venue":"Synthetic %s Review"%topic_label.title(),
                "venue_type":"journal",
                "language":"en",
                "source":"benchmark",
                "source_id":"large-%05d"%serial,
            }
            if per_topic%10==0:
                fulltext,anchor=_make_fulltext(topic_label,topic_words,serial,rng)
                row["abstract"]=_make_abstract(topic_label,topic_words,serial,rng)
                row["fulltext"]=fulltext
                row["extra"]={"content_level":"fulltext","topic":topic_label,"per_topic":per_topic}
                fulltext_anchors.append((doc_id,anchor,topic_label))
                topic_fulltext_ids[topic_label].append(doc_id)
            elif per_topic%10 in(1,2):
                row["extra"]={"content_level":"title","topic":topic_label,"per_topic":per_topic}
            else:
                row["abstract"]=_make_abstract(topic_label,topic_words,serial,rng)
                row["extra"]={"content_level":"abstract","topic":topic_label,"per_topic":per_topic}
            handle.write(json.dumps(row,ensure_ascii=False)+"\n")

    for index,(doc_id,anchor,_topic) in enumerate(fulltext_anchors):
        split="calibration" if index%3==0 else "test"
        query=anchor
        query_type="exact"
        if index%4==1:
            words=query.split()
            query="  \n".join(words)
            query_type="exact_whitespace"
        elif index%4==2:
            query=" ".join(query.split()[:5])
            query_type="short_exact"
        query_rows.append(GeneratedCase(
            id="q-%05d"%index,
            query=query,
            query_type=query_type,
            split=split,
            should_find=True,
            expected_doc_ids=[doc_id],
            language="en",
        ))

    for topic_label,topic_words in TOPICS:
        expected_doc_ids=topic_fulltext_ids.get(topic_label) or[]
        if not expected_doc_ids:
            continue
        query_rows.append(GeneratedCase(
            id="search-%s"%re.sub(r"[^a-z0-9]+","-",topic_label),
            query="%s %s %s"%(topic_label,topic_words[0],topic_words[1]),
            query_type="topic_search",
            split="test",
            should_find=True,
            expected_doc_ids=list(expected_doc_ids),
            language="en",
        ))
        query_rows.append(GeneratedCase(
            id="negative-topic-%s"%re.sub(r"[^a-z0-9]+","-",topic_label),
            query=(
                "A synthetic negative statement about %s without any source sentence, "
                "unique marker neg-%s, should not be confirmed as local evidence."
                %(topic_label,re.sub(r"[^a-z0-9]+","-",topic_label))
            ),
            query_type="hard_negative",
            split="test",
            should_find=False,
            expected_doc_ids=[],
            language="en",
        ))
        query_rows.append(GeneratedCase(
            id="unrelated-%s"%re.sub(r"[^a-z0-9]+","-",topic_label),
            query=(
                "Unrelated calibration sentence zeta-%s describes a fictional "
                "procedure with no relation to the indexed benchmark corpus."
                %re.sub(r"[^a-z0-9]+","-",topic_label)
            ),
            query_type="unrelated",
            split="test",
            should_find=False,
            expected_doc_ids=[],
            language="en",
        ))

    with open(queries_path,"w",encoding="utf-8") as handle:
        for case in query_rows:
            handle.write(json.dumps(case.to_dict(),ensure_ascii=False)+"\n")

    corpus_counts={
        "documents":document_count,
        "fulltext":sum(1 for index in range(document_count) if (index//len(TOPICS))%10==0),
        "abstract":sum(1 for index in range(document_count) if (index//len(TOPICS))%10 not in(0,1,2)),
        "title_only":sum(1 for index in range(document_count) if (index//len(TOPICS))%10 in(1,2)),
    }
    manifest={
        "benchmark_version":BENCHMARK_VERSION,
        "created_at":"2026-11-18",
        "seed":seed,
        "corpus_file":"corpus.jsonl",
        "queries_file":"queries.jsonl",
        "corpus_sha256":_sha256(corpus_path),
        "queries_sha256":_sha256(queries_path),
        "documents":document_count,
        "topics":len(TOPICS),
        "queries":len(query_rows),
        "content_counts":corpus_counts,
        "query_types":dict(sorted(
            (kind,sum(1 for case in query_rows if case.query_type==kind))
            for kind in {case.query_type for case in query_rows}
        )),
        "frozen":True,
    }
    (output_dir/"manifest.json").write_text(
        json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",
        encoding="utf-8",
    )
    return manifest


def _percentile(values:Sequence[float],ratio:float)->float:
    if not values:
        return 0.0
    ordered=sorted(values)
    index=max(0,min(len(ordered)-1,int(math.ceil(ratio*len(ordered)))-1))
    return ordered[index]


def run_benchmark(benchmark_dir:Path)->dict:
    """在冻结语料上运行核心链路，产物只写测评报告。"""
    manifest=json.loads((benchmark_dir/"manifest.json").read_text(encoding="utf-8"))
    corpus=benchmark_dir/manifest["corpus_file"]
    queries=benchmark_dir/manifest["queries_file"]
    if _sha256(corpus)!=manifest["corpus_sha256"]:
        raise RuntimeError("冻结语料哈希不一致")
    if _sha256(queries)!=manifest["queries_sha256"]:
        raise RuntimeError("冻结查询哈希不一致")

    started=time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="litagent-large-benchmark-") as temp:
        store,settings=build_evaluation_store(corpus,Path(temp))
        build_seconds=time.perf_counter()-started
        try:
            cases=load_cases(queries)
            tracer=QuoteTracer(store,settings)
            searcher=HybridSearcher(store,settings)
            trace_metrics=defaultdict(lambda:{"total":0,"correct":0,"wrong":0,"miss":0})
            trace_times=[]
            search_total=search_correct=0

            for case in cases:
                case_started=time.perf_counter()
                if case.query_type=="topic_search":
                    results,_=searcher.search_documents(case.query,top_k=10)
                    expected=set(case.expected_doc_ids)
                    search_total+=1
                    if any(doc.id in expected for doc,_,_ in results):
                        search_correct+=1
                    trace_times.append(time.perf_counter()-case_started)
                    continue
                result,_=tracer.trace(case.query,allow_online=False)
                elapsed=time.perf_counter()-case_started
                trace_times.append(elapsed)
                row=trace_metrics[case.query_type]
                row["total"]+=1
                actual=result.hit.doc_id if result.hit is not None else ""
                if result.found and actual in set(case.expected_doc_ids):
                    row["correct"]+=1
                elif result.found:
                    row["wrong"]+=1
                elif case.should_find:
                    row["miss"]+=1

            checks=dict(store.connect().execute("PRAGMA integrity_check").fetchone())
            stats=store.stats()
            orphan_queries={
                "postings":(
                    "SELECT COUNT(*) n FROM postings p LEFT JOIN chunks c "
                    "ON c.id=p.chunk_id WHERE c.id IS NULL"
                ),
                "fingerprints":(
                    "SELECT COUNT(*) n FROM fingerprints f LEFT JOIN chunks c "
                    "ON c.id=f.chunk_id WHERE c.id IS NULL"
                ),
                "chunk_text":(
                    "SELECT COUNT(*) n FROM chunk_text t LEFT JOIN chunks c "
                    "ON c.id=t.chunk_id WHERE c.id IS NULL"
                ),
            }
            orphans={
                key:store.connect().execute(sql).fetchone()["n"]
                for key,sql in orphan_queries.items()
            }
            total_trace=sum(row["total"] for row in trace_metrics.values())
            correct_trace=sum(row["correct"] for row in trace_metrics.values())
            wrong_trace=sum(row["wrong"] for row in trace_metrics.values())
            missed_trace=sum(row["miss"] for row in trace_metrics.values())
            report={
                "benchmark_version":manifest["benchmark_version"],
                "corpus_sha256":manifest["corpus_sha256"],
                "queries_sha256":manifest["queries_sha256"],
                "documents":stats["documents"],
                "fulltext_documents":stats["documents_with_fulltext"],
                "chunks":stats["chunks"],
                "postings":stats["postings"],
                "fingerprints":stats["fingerprints"],
                "build_seconds":round(build_seconds,3),
                "trace_queries":total_trace,
                "trace_correct":correct_trace,
                "trace_wrong":wrong_trace,
                "trace_missed":missed_trace,
                "trace_precision":round(
                    correct_trace/float(correct_trace+wrong_trace),4
                ) if correct_trace+wrong_trace else 0.0,
                "trace_recall":round(
                    correct_trace/float(correct_trace+missed_trace),4
                ) if correct_trace+missed_trace else 0.0,
                "by_query_type":dict(trace_metrics),
                "topic_search_total":search_total,
                "topic_search_correct":search_correct,
                "topic_search_recall_at_10":round(
                    search_correct/float(search_total),4
                ) if search_total else 0.0,
                "latency_ms":{
                    "p50":round(_percentile(trace_times,0.50)*1000,3),
                    "p95":round(_percentile(trace_times,0.95)*1000,3),
                    "max":round(max(trace_times,default=0.0)*1000,3),
                },
                "integrity_check":checks.get("integrity_check"),
                "orphans":orphans,
                "database_size_bytes":stats["db_size_bytes"],
            }
        finally:
            store.close_all()
    return report


def format_markdown(report:dict)->str:
    lines=[
        "# lit-agent 大规模固定基准测试",
        "",
        "- 基准版本：%s"%report["benchmark_version"],
        "- 文献：%d 篇"%report["documents"],
        "- 全文文献：%d 篇"%report["fulltext_documents"],
        "- 片段：%d 个"%report["chunks"],
        "- BM25 posting：%d"%report["postings"],
        "- 精确指纹：%d"%report["fingerprints"],
        "- 构建耗时：%.3f 秒"%report["build_seconds"],
        "",
        "## 溯源",
        "",
        "| 指标 | 结果 |",
        "|---|---:|",
        "| 查询 | %d |"%report["trace_queries"],
        "| 正确确认 | %d |"%report["trace_correct"],
        "| 错误确认 | %d |"%report["trace_wrong"],
        "| 漏报 | %d |"%report["trace_missed"],
        "| 精确率 | %.4f |"%report["trace_precision"],
        "| 召回率 | %.4f |"%report["trace_recall"],
        "",
        "## 主题检索",
        "",
        "- Recall@10：%.4f（%d/%d）"
        %(report["topic_search_recall_at_10"],
          report["topic_search_correct"],report["topic_search_total"]),
        "",
        "## 延迟",
        "",
        "- P50：%.3f ms"%report["latency_ms"]["p50"],
        "- P95：%.3f ms"%report["latency_ms"]["p95"],
        "- 最大：%.3f ms"%report["latency_ms"]["max"],
        "",
        "## 完整性",
        "",
        "- integrity_check：%s"%report["integrity_check"],
        "- orphans：%s"%json.dumps(report["orphans"],ensure_ascii=False),
    ]
    return "\n".join(lines)+"\n"


def main(argv:Optional[Sequence[str]]=None)->int:
    parser=argparse.ArgumentParser(description="大规模 lit-agent 固定基准")
    sub=parser.add_subparsers(dest="command",required=True)
    gen=sub.add_parser("generate")
    gen.add_argument("--output",required=True)
    gen.add_argument("--documents",type=int,default=DEFAULT_DOCUMENT_COUNT)
    run=sub.add_parser("run")
    run.add_argument("--benchmark",required=True)
    run.add_argument("--output",required=True)
    args=parser.parse_args(argv)
    if args.command=="generate":
        manifest=generate_benchmark(Path(args.output),document_count=args.documents)
        print(json.dumps(manifest,ensure_ascii=False,indent=2))
        return 0
    report=run_benchmark(Path(args.benchmark))
    target=Path(args.output)
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    target.with_suffix(".md").write_text(format_markdown(report),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0


if __name__=="__main__":
    raise SystemExit(main())


__all__=[
    "BENCHMARK_VERSION",
    "TOPICS",
    "generate_benchmark",
    "run_benchmark",
    "format_markdown",
]
