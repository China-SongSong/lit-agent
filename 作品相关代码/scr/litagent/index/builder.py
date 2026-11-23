"""为清洗后的片段建立 BM25、精确指纹和本地向量索引。"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence

from litagent.config import Settings
from litagent.config import settings as default_settings
from litagent.index.lexical import build_term_frequencies
from litagent.index.phrase import build_fingerprints
from litagent.index.store import Store
from litagent.index.vector import get_embedding_encoder


@dataclass
class IndexReport:
    """建索引任务报告。"""

    docs_processed:int=0
    docs_skipped:int=0
    chunks_indexed:int=0
    postings_written:int=0
    fingerprints_written:int=0
    vectors_written:int=0
    errors:List[str]=field(default_factory=list)
    elapsed_seconds:float=0.0

    def summary(self)->str:
        lines=[
            "建索引报告",
            "  处理文献   : %d 篇（跳过已建好的 %d 篇）"
            %(self.docs_processed,self.docs_skipped),
            "  索引片段   : %d 个"%self.chunks_indexed,
            "  BM25 倒排  : %d 条 posting"%self.postings_written,
            "  精确指纹   : %d 条"%self.fingerprints_written,
            "  向量片段   : %d 个"%self.vectors_written,
        ]
        lines.append("  耗时       : %.1f 秒"%self.elapsed_seconds)
        if self.errors:
            lines.append("  错误样例   :")
            for error in self.errors[:8]:
                lines.append("    - "+error)
        return "\n".join(lines)


class IndexBuilder:
    """建索引调度器。"""

    def __init__(self,store:Store,settings:Optional[Settings]=None)->None:
        self.store=store
        self.settings=settings or default_settings
        self.embedding_encoder=get_embedding_encoder(
            backend=self.settings.vector_backend,
            model_dir=self.settings.vector_model_dir,
            batch_size=self.settings.vector_batch_size,
            dimension=self.settings.vector_dim,
        )
        self.vector_model=self.embedding_encoder.model
        self.vector_dim=self.embedding_encoder.dimension

    # ------------------------------------------------------------------
    # 稀疏索引（BM25 + 精确指纹）
    # ------------------------------------------------------------------
    def build_lexical(self,*,doc_ids:Optional[Sequence[str]]=None,
                      rebuild:bool=False,
                      on_progress:Optional[Callable[[int,int,str],None]]=None,
                      report:Optional[IndexReport]=None)->IndexReport:
        """为片段建立 BM25 倒排表与精确指纹。

        ``rebuild=False`` 时只处理缺倒排或缺指纹的文献（增量）。
        """
        report=report or IndexReport()
        if doc_ids:
            targets=list(doc_ids)
        elif rebuild:
            rows=self.store.connect().execute(
                "SELECT DISTINCT doc_id AS doc_id FROM chunks ORDER BY doc_id"
            ).fetchall()
            targets=[row["doc_id"] for row in rows]
        else:
            targets=self.store.docs_needing_lexical_index(
                fingerprint_n=self.settings.fingerprint_n
            )

        total=len(targets)
        cache_dir=self.settings.cache_dir

        for index,doc_id in enumerate(targets,start=1):
            try:
                if not rebuild and self.store.doc_has_postings(doc_id) \
                        and self.store.doc_has_fingerprints(doc_id):
                    report.docs_skipped+=1
                    continue

                chunks=list(self.store.iter_chunks(doc_id))
                if not chunks:
                    report.docs_skipped+=1
                    continue

                term_freqs=build_term_frequencies(chunks,cache_dir=cache_dir)
                fingerprints=build_fingerprints(
                    chunks,n=self.settings.fingerprint_n
                )

                # replace_chunks 是幂等的：先清空该文献的旧索引再写入，
                # 因此重复调用不会留下脏数据
                self.store.replace_chunks(
                    doc_id,chunks,
                    term_freqs=term_freqs,fingerprints=fingerprints,
                )

                report.docs_processed+=1
                report.chunks_indexed+=len(chunks)
                report.postings_written+=sum(len(v) for v in term_freqs.values())
                report.fingerprints_written+=sum(len(v) for v in fingerprints.values())
            except Exception as exc:  # noqa: BLE001
                report.errors.append("%s: %s: %s"%(
                    doc_id,type(exc).__name__,str(exc)[:150]))

            if on_progress:
                try:
                    on_progress(index,total,doc_id)
                except Exception:  # noqa: BLE001
                    pass

        self.store.set_meta("fingerprint_n",str(self.settings.fingerprint_n))
        self.store.set_meta("bm25_k1",str(self.settings.bm25_k1))
        self.store.set_meta("bm25_b",str(self.settings.bm25_b))
        return report

    def build_vectors(
        self,*,
        doc_ids:Optional[Sequence[str]]=None,
        rebuild:bool=False,
        on_progress:Optional[Callable[[int,int,str],None]]=None,
        report:Optional[IndexReport]=None,
    )->IndexReport:
        """为片段建立本地语义向量；默认只补缺失或内容哈希变化的片段。"""
        report=report or IndexReport()
        if not self.settings.vector_enabled:
            return report
        if doc_ids:
            targets=list(doc_ids)
        elif rebuild:
            rows=self.store.connect().execute(
                "SELECT DISTINCT doc_id AS doc_id FROM chunks ORDER BY doc_id"
            ).fetchall()
            targets=[row["doc_id"] for row in rows]
        else:
            targets=self.store.docs_needing_vector_index(
                model=self.vector_model,dimension=self.vector_dim
            )

        total=len(targets)
        for index,doc_id in enumerate(targets,start=1):
            try:
                chunks=list(self.store.iter_chunks(doc_id))
                if not chunks:
                    continue
                encoded=self.embedding_encoder.encode(
                    [chunk.text for chunk in chunks]
                )
                vectors={
                    chunk.id:vector.tobytes()
                    for chunk,vector in zip(chunks,encoded)
                }
                self.store.replace_chunk_vectors(
                    doc_id,vectors,
                    model=self.vector_model,
                    dimension=self.vector_dim,
                )
                report.vectors_written+=len(vectors)
            except Exception as exc:  # noqa: BLE001
                report.errors.append("%s: %s: %s"%(
                    doc_id,type(exc).__name__,str(exc)[:150]
                ))
            if on_progress:
                try:
                    on_progress(index,total,doc_id)
                except Exception:  # noqa: BLE001
                    pass
        self.store.set_meta("vector_model",self.vector_model)
        self.store.set_meta("vector_dim",str(self.vector_dim))
        return report

    # ------------------------------------------------------------------
    # 组合
    # ------------------------------------------------------------------
    def build_all(self,*,doc_ids:Optional[Sequence[str]]=None,
                  rebuild:bool=False,
                  on_progress:Optional[Callable[[str,int,int],None]]=None
                  )->IndexReport:
        """一次建好词法、精确指纹与本地向量索引。"""
        started=time.monotonic()
        report=IndexReport()

        def lexical_progress(index:int,total:int,doc_id:str)->None:
            if on_progress:
                on_progress("lexical",index,total)

        self.build_lexical(doc_ids=doc_ids,rebuild=rebuild,
                           on_progress=lexical_progress,report=report)

        def vector_progress(index:int,total:int,doc_id:str)->None:
            if on_progress:
                on_progress("vector",index,total)

        self.build_vectors(
            doc_ids=doc_ids,rebuild=rebuild,
            on_progress=vector_progress,report=report,
        )

        self.store.optimize()
        report.elapsed_seconds=time.monotonic()-started
        return report


__all__=["IndexBuilder","IndexReport"]
