"""构建隔离评测语料，并校准引文溯源的确认阈值。

评测库始终在临时目录中重建，不读写生产索引。该工具刻意不调用任何在线
来源，用于先固定本地指纹、BM25 与负样本基线，再评估后续向量检索和
OpenCLI 是否带来真实增量。
"""
from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

from litagent.clean.pipeline import CleanPipeline
from litagent.config import Settings
from litagent.contracts import CONTRACT_VERSION
from litagent.index.builder import IndexBuilder
from litagent.index.store import Store
from litagent.retrieve.trace import DEFAULT_FOUND_THRESHOLD, QuoteTracer
from litagent.schema import Document

DEFAULT_THRESHOLDS=(0.60,0.64,0.68,0.70,0.72,0.74,0.76,0.80)


@dataclass(frozen=True)
class EvaluationCase:
    """一条带金标准的离线溯源样本。"""

    id:str
    query:str
    query_type:str
    split:str
    should_find:bool
    expected_doc_ids:List[str]=field(default_factory=list)
    language:str=""


@dataclass
class ThresholdMetrics:
    """某个确认阈值下的混淆矩阵与核心指标。"""

    threshold:float
    total:int=0
    correct_found:int=0
    wrong_found:int=0
    missed:int=0
    correct_abstain:int=0
    bm25_support:int=0
    precision:float=0.0
    recall:float=0.0
    false_source_rate:float=0.0

    def to_dict(self)->dict:
        return asdict(self)


@dataclass
class CalibrationReport:
    contract_version:str
    corpus_version:str
    cases:int
    thresholds:List[ThresholdMetrics]
    by_split:dict
    candidate_threshold:Optional[float]
    runtime_default_threshold:float
    adoption_status:str
    recommendation_policy:str

    def to_dict(self)->dict:
        return{
            "contract_version":self.contract_version,
            "corpus_version":self.corpus_version,
            "cases":self.cases,
            "thresholds":[row.to_dict() for row in self.thresholds],
            "by_split":self.by_split,
            "candidate_threshold":self.candidate_threshold,
            "runtime_default_threshold":self.runtime_default_threshold,
            "adoption_status":self.adoption_status,
            "recommendation_policy":self.recommendation_policy,
        }


def read_jsonl(path:Path)->List[dict]:
    rows:List[dict]=[]
    with open(path,"r",encoding="utf-8") as handle:
        for line_number,line in enumerate(handle,start=1):
            line=line.strip()
            if not line:
                continue
            try:
                value=json.loads(line)
            except ValueError as exc:
                raise ValueError("%s 第 %d 行不是合法 JSON：%s"%(path,line_number,exc)) from exc
            if not isinstance(value,dict):
                raise ValueError("%s 第 %d 行必须是 JSON 对象"%(path,line_number))
            rows.append(value)
    return rows


def load_corpus(path:Path)->List[Document]:
    documents=[]
    for row in read_jsonl(path):
        doc=Document.from_dict(row)
        if doc.is_empty():
            raise ValueError("评测语料存在空标题文献：%r"%row.get("id"))
        documents.append(doc)
    return documents


def load_cases(path:Path)->List[EvaluationCase]:
    cases=[]
    for row in read_jsonl(path):
        expected=row.get("expected_doc_ids") or[]
        if isinstance(expected,str):
            expected=[expected]
        cases.append(EvaluationCase(
            id=str(row.get("id") or ""),
            query=str(row.get("query") or ""),
            query_type=str(row.get("query_type") or "unknown"),
            split=str(row.get("split") or "test"),
            should_find=bool(row.get("should_find")),
            expected_doc_ids=[str(value) for value in expected if value],
            language=str(row.get("language") or ""),
        ))
    return cases


def build_evaluation_store(corpus_path:Path,workdir:Path)->tuple[Store,Settings]:
    """从 JSONL 在隔离目录中构建可检索的评测库。"""
    settings=Settings(
        project_root=workdir,
        data_dir=workdir/"data",
        deps_dir=workdir/".deps",
        fingerprint_n=7,
        max_chunk_chars=1800,
        chunk_overlap_chars=200,
    )
    settings.ensure_dirs()
    store=Store(settings.db_path)
    store.init_schema()

    documents=load_corpus(corpus_path)
    store.upsert_documents(documents)
    for doc in documents:
        if(doc.fulltext or "").strip():
            store.set_fulltext(
                doc.id,
                doc.fulltext or "",
                mark_fulltext=(
                    (doc.extra or {}).get("content_level","fulltext")=="fulltext"
                ),
            )
    pipeline=CleanPipeline(store,settings)
    clean_report=pipeline.run(redo=True)
    if clean_report.failed:
        raise RuntimeError("评测语料清洗失败：%s"%clean_report.summary())
    index_report=IndexBuilder(store,settings).build_all(rebuild=True)
    if index_report.errors:
        raise RuntimeError("评测索引构建失败：%s"%index_report.summary())
    return store,settings


def _score_rows(
    cases:Sequence[EvaluationCase],
    tracer:QuoteTracer,
    threshold:float,
)->ThresholdMetrics:
    row=ThresholdMetrics(threshold=threshold,total=len(cases))
    for case in cases:
        result,report=tracer.trace(
            case.query,
            allow_online=False,
            found_threshold=threshold,
        )
        row.bm25_support+=int(report.bm25_support)
        expected=set(case.expected_doc_ids)
        actual=result.hit.doc_id if result.hit is not None else ""
        correct=bool(result.found and actual in expected)

        if correct:
            row.correct_found+=1
        elif result.found:
            row.wrong_found+=1
        elif case.should_find:
            row.missed+=1
        else:
            row.correct_abstain+=1

    found_count=row.correct_found+row.wrong_found
    expected_count=sum(1 for case in cases if case.should_find)
    row.precision=row.correct_found/float(found_count) if found_count else 0.0
    row.recall=row.correct_found/float(expected_count) if expected_count else 0.0
    row.false_source_rate=row.wrong_found/float(found_count) if found_count else 0.0
    return row


def _build_report(
    cases:Sequence[EvaluationCase],
    tracer:QuoteTracer,
    thresholds:Sequence[float],
    *,
    corpus_version:str,
    minimum_precision:float,
)->CalibrationReport:
    rows=[_score_rows(cases,tracer,float(value)) for value in thresholds]
    by_split={}
    for split in sorted({case.split for case in cases}):
        split_cases=[case for case in cases if case.split==split]
        by_split[split]=[
            _score_rows(split_cases,tracer,float(value)).to_dict()
            for value in thresholds
        ]

    eligible=[
        row for row in rows
        if row.precision>=minimum_precision and row.correct_found>0
    ]
    if eligible:
        best_recall=max(row.recall for row in eligible)
        shortlist=[row for row in eligible if row.recall==best_recall]
        candidate=min(
            shortlist,
            key=lambda row:abs(row.threshold-DEFAULT_FOUND_THRESHOLD),
        ).threshold
    else:
        candidate=None
    return CalibrationReport(
        contract_version=CONTRACT_VERSION,
        corpus_version=corpus_version,
        cases=len(cases),
        thresholds=rows,
        by_split=by_split,
        candidate_threshold=candidate,
        runtime_default_threshold=DEFAULT_FOUND_THRESHOLD,
        adoption_status=(
            "not_adopted_pending_more_boundary_and_negative_samples"
        ),
        recommendation_policy=(
            "precision>=%.2f 后最大化 recall；recall 相同时选择最接近"
            "现有基线 %.2f 的阈值"
            %(minimum_precision,DEFAULT_FOUND_THRESHOLD)
        ),
    )


def calibrate(
    corpus_path:Path,
    queries_path:Path,
    *,
    thresholds:Sequence[float]=DEFAULT_THRESHOLDS,
    minimum_precision:float=0.98,
)->CalibrationReport:
    """在同一固定语料上扫描确认阈值。

    推荐阈值只在 precision 达标后比较 recall；精度相同时优先保留更高的
    阈值，避免为了微小召回提升无必要地放宽误报边界。
    """
    cases=load_cases(queries_path)
    with tempfile.TemporaryDirectory(prefix="litagent-eval-") as temp:
        store,settings=build_evaluation_store(corpus_path,Path(temp))
        try:
            tracer=QuoteTracer(store,settings)
            return _build_report(
                cases,
                tracer,
                thresholds,
                corpus_version="%s|%s"%(corpus_path.stem,queries_path.stem),
                minimum_precision=minimum_precision,
            )
        finally:
            store.close_all()


def calibrate_database(
    db_path:Path,
    queries_path:Path,
    *,
    thresholds:Sequence[float]=DEFAULT_THRESHOLDS,
    minimum_precision:float=0.98,
)->CalibrationReport:
    """只读校准一个既有数据库，不创建或修改任何索引。"""
    cases=load_cases(queries_path)
    store=Store(db_path,read_only=True)
    try:
        settings=Settings(
            project_root=db_path.resolve().parent,
            data_dir=db_path.resolve().parent,
            deps_dir=db_path.resolve().parent/"deps-unused",
        )
        tracer=QuoteTracer(store,settings)
        return _build_report(
            cases,
            tracer,
            thresholds,
            corpus_version="db:%s|%s"%(db_path.name,queries_path.stem),
            minimum_precision=minimum_precision,
        )
    finally:
        store.close_all()


def format_markdown(report:CalibrationReport)->str:
    lines=[
        "# 引文溯源阈值校准",
        "",
        "- 契约版本：%s"%report.contract_version,
        "- 评测集：%s"%report.corpus_version,
        "- 样本数：%d"%report.cases,
        "- 校准观察阈值：%s"%(
            "%.2f"%report.candidate_threshold
            if report.candidate_threshold is not None else "无满足精度要求的阈值"
        ),
        "- 运行时默认阈值：%.2f（本轮不自动修改）"
        %report.runtime_default_threshold,
        "- 采纳状态：%s"%report.adoption_status,
        "",
        "| 阈值 | 正确确认 | 错误确认 | 漏报 | 正确拒绝 | 精确率 | 召回率 | 错误出处率 | BM25旁证 |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report.thresholds:
        lines.append(
            "| %.2f | %d | %d | %d | %d | %.3f | %.3f | %.3f | %d |"
            %(row.threshold,row.correct_found,row.wrong_found,row.missed,
              row.correct_abstain,row.precision,row.recall,
              row.false_source_rate,row.bm25_support)
        )
    lines.extend(["", "说明：BM25 仅作为诊断计数，不参与置信度或 found 判定。"])
    return "\n".join(lines)+"\n"


def main(argv:Optional[Sequence[str]]=None)->int:
    parser=argparse.ArgumentParser(description="离线校准 lit-agent 的溯源阈值")
    source_group=parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--corpus",help="评测语料 JSONL；在临时目录重建评测库")
    source_group.add_argument("--db",help="既有 SQLite 索引；以只读模式校准")
    parser.add_argument("--queries",required=True,help="带金标准的查询 JSONL")
    parser.add_argument("--thresholds",default=",".join(str(v) for v in DEFAULT_THRESHOLDS))
    parser.add_argument("--minimum-precision",type=float,default=0.98)
    parser.add_argument("--format",choices=("markdown","json"),default="markdown")
    parser.add_argument("--output",help="可选输出文件；缺省写到 stdout")
    args=parser.parse_args(argv)
    thresholds=[float(value.strip()) for value in args.thresholds.split(",") if value.strip()]
    if args.db:
        report=calibrate_database(
            Path(args.db),
            Path(args.queries),
            thresholds=thresholds,
            minimum_precision=args.minimum_precision,
        )
    else:
        report=calibrate(
            Path(args.corpus),
            Path(args.queries),
            thresholds=thresholds,
            minimum_precision=args.minimum_precision,
        )
    text=(
        json.dumps(report.to_dict(),ensure_ascii=False,indent=2)+"\n"
        if args.format=="json" else format_markdown(report)
    )
    if args.output:
        target=Path(args.output)
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(text,encoding="utf-8")
    else:
        print(text,end="")
    return 0


if __name__=="__main__":
    raise SystemExit(main())


__all__=[
    "DEFAULT_THRESHOLDS",
    "EvaluationCase",
    "ThresholdMetrics",
    "CalibrationReport",
    "read_jsonl",
    "load_corpus",
    "load_cases",
    "build_evaluation_store",
    "calibrate",
    "calibrate_database",
    "format_markdown",
    "main",
]
