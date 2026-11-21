"""批量总结文献，并复用已有缓存。"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor,as_completed
from dataclasses import dataclass,field
from typing import Any,Callable,List,Optional,Sequence

from litagent.config import Settings,settings as default_settings
from litagent.index.store import Store
from litagent.schema import Document,Summary,utcnow
from litagent.summarize.extractor import SummaryExtractor
from litagent.summarize.prompts import PROMPT_VERSION

@dataclass
class SummarizeReport:
    """批量总结报告。"""

    total:int=0
    processed:int=0  #字段 processed
    skipped:int=0
    deferred:int=0  #超过 limit 而本次未处理的待办数，和“跳过已有”分开统计
    failed:int=0
    claims_extracted:int=0
    prompt_tokens:int=0  #字段 prompt_tokens
    completion_tokens:int=0
    calls:int=0
    cache_hits:int=0
    errors:List[str]=field(default_factory=list)  #用 field 加工厂函数生成独立列表
    elapsed_seconds:float=0.0

    def summary(self)->str:
        lines=[
            "结构化总结报告",
            "  候选文献   : %d 篇"%self.total,
            "  成功总结   : %d 篇（跳过已有 %d，未处理 %d，失败 %d）"
            %(self.processed,self.skipped,self.deferred,self.failed),  #承接上一行字符串，补上格式化要填的四个值
            "  摘出论断   : %d 条"%self.claims_extracted,
            "  LLM 调用   : %d 次（缓存命中 %d 次）"%(self.calls,self.cache_hits),
            "  Token 用量 : 输入 %d，输出 %d"
            %(self.prompt_tokens,self.completion_tokens),
            "  耗时       : %.1f 秒"%self.elapsed_seconds,
        ]
        if self.elapsed_seconds>0 and self.processed:
            lines.append("  平均每篇   : %.1f 秒"  #追加平均每篇耗时那一行，先放好模板文字
                         %(self.elapsed_seconds/self.processed))
        if self.errors:
            lines.append("  错误样例   :")
            for error in self.errors[:8]:  #只取前 8 条错误
                lines.append("    - "+error[:180])
        return "\n".join(lines)

class SummaryRunner:
    """批量总结调度器。"""

    def __init__(self,store:Store,settings:Optional[Settings]=None)->None:
        self.store=store
        self.settings=settings or default_settings
        self.extractor:Optional[SummaryExtractor]=None

    def _get_extractor(self)->SummaryExtractor:
        if self.extractor is None:
            self.extractor=SummaryExtractor(self.store,self.settings)
        return self.extractor

    #注释文字：这一节名字叫批量
    def run(self,*,doc_ids:Optional[Sequence[str]]=None,
            limit:Optional[int]=None,
            redo:bool=False,  #参数 redo：为真表示重做，忽略已有总结
            with_claims:bool=True,
            concurrency:Optional[int]=None,
            on_progress:Optional[Callable[[int,int,str],None]]=None
            )->SummarizeReport:  #注解说明 run 返回总结报告对象
        """批量总结。"""
        started=time.monotonic()
        report=SummarizeReport()

        if doc_ids:
            targets:List[Document]=[
                doc for doc in(self.store.get_document(i,with_fulltext=False)
                                for i in doc_ids) if doc is not None
            ]
        elif redo:
            targets=[doc for doc in self.store.iter_documents(limit=limit)  #遍历全部文献（可受 limit 限制）
                       if self._has_content(doc)]
        else:
            all_pending_ids=self.store.docs_needing_summary(
                PROMPT_VERSION,only_with_fulltext=True  #先取全量待办，才能区分“已总结”和“本次未处理”
            )
            pending_ids=all_pending_ids[:limit] if limit else all_pending_ids
            targets=[doc for doc in(self.store.get_document(i,with_fulltext=False)
                                       for i in pending_ids) if doc is not None]
            report.skipped=max(  #开始计算跳过多少篇
                0,self.store.count_documents(only_with_fulltext=True)-len(all_pending_ids)
            )
            report.deferred=max(0,len(all_pending_ids)-len(targets))  #超出 limit 的部分只是本次未处理

        report.total=len(targets)
        if not targets:
            report.elapsed_seconds=time.monotonic()-started
            return report

        workers=max(1,concurrency or self.settings.llm_max_concurrency)  #决定线程数：传了就用传入值，否则用配置并发数
        extractor=self._get_extractor()
        completed=0

        def handle(doc:Document)->Optional[Summary]:  #定义交给线程池调用的处理函数：输入文献
            #--redo 的语义是“重新生成”，必须绕过同一内容的旧缓存；否则旧的不完整总结会被原样读回。
            return extractor.extract(doc,with_claims=with_claims,use_cache=not redo)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures={pool.submit(handle,doc):doc for doc in targets}  #一次把所有任务提交完，用字典把未来对象映射回文献
            for future in as_completed(futures):
                doc=futures[future]
                completed+=1
                try:  #开始异常保护
                    summary=future.result()
                    if summary is None or summary.is_empty():
                        report.failed+=1
                        report.errors.append("%s：模型返回空结果"%doc.id)  #把失败原因记进报告，编号后用中文冒号分隔
                    else:
                        existing=self.store.get_summary(doc.id)
                        final=summary
                        if existing is not None and not existing.is_empty():
                            #重生成结果覆盖度更低时保留旧字段，只合并新论断和缺失字段。
                            if summary.structured_field_count()>=existing.structured_field_count():
                                final=summary.merge(existing)
                            else:
                                final=existing.merge(summary)
                        self.store.upsert_summary(final)
                        self.store.set_meta("summarize_prompt_version",PROMPT_VERSION)
                        report.processed+=1  #成功计数加一
                        report.claims_extracted+=len(final.claims)
                        doc.summarized_at=utcnow()
                        self.store.upsert_document(doc)
                except Exception as exc:
                    report.failed+=1
                    report.errors.append("%s: %s: %s"
                                         %(doc.id,type(exc).__name__,str(exc)[:150]))  #补上三个待填参数；异常文本截到 150 字符

                if on_progress:
                    try:
                        on_progress(completed,report.total,doc.title[:60])  #回调收到已完成数、总数与文献标题
                    except Exception:
                        pass

        client=getattr(extractor,"_client",None)
        if client is not None:
            usage=client.usage
            report.calls=usage.calls
            report.cache_hits=usage.cached_calls
            report.prompt_tokens=usage.prompt_tokens
            report.completion_tokens=usage.completion_tokens  #写入输出 token 总数
        else:
            report.cache_hits=getattr(extractor,"_cached_calls",0)  #纯缓存运行不构造客户端
        report.elapsed_seconds=time.monotonic()-started
        return report

    def _has_content(self,doc:Document)->bool:
        """判断这篇文献值不值得送进模型（没有内容就是白花钱）。"""
        if(doc.abstract or "").strip():
            return True
        if(doc.extra or{}).get("chunk_count"):  #再看附加信息里的片段数量
            return True
        return self.store.has_fulltext(doc.id)

    #注释文字：这一节名字叫导出
    def export_summaries(self,path:Optional[Any]=None,
                         limit:Optional[int]=None)->str:
        """把所有总结导出成 JSONL，方便外部工具消费或人工抽查。"""
        import json
        from pathlib import Path

        target=Path(path) if path else(self.settings.summary_dir/"summaries.jsonl")  #决定输出路径：传了就用传入路径
        target.parent.mkdir(parents=True,exist_ok=True)

        count=0
        with open(target,"w",encoding="utf-8") as handle:  #以写模式打开文件并指定 UTF-8 编码
            for row in self.store.connect().execute(
                "SELECT * FROM summaries ORDER BY doc_id"
            ):
                summary=Store._row_to_summary(row)  #用静态方法把数据库行还原成总结对象
                doc=self.store.get_document(summary.doc_id,with_fulltext=False)
                payload=summary.to_dict()
                payload["citation"]=self._citation_of(doc)
                payload["title"]=doc.title if doc else ""  #补上标题；查不到文献时退化成空字符串
                handle.write(json.dumps(payload,ensure_ascii=False)+"\n")
                count+=1
                if limit and count>=limit:
                    break  #跳出循环，不再继续从库里读
        return str(target)

    @staticmethod
    def _citation_of(doc:Optional[Document])->str:
        if doc is None:
            return ""
        try:
            from litagent.cite.gbt7714 import format_gbt7714

            return format_gbt7714(doc.to_citation_dict())
        except Exception:
            return ""

    def close(self)->None:
        if self.extractor is not None:
            self.extractor.close()

__all__=["SummaryRunner","SummarizeReport"]
