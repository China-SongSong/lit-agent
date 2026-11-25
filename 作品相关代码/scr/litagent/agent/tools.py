"""Agent 可以调用的文献工具。

工具声明单独保存在 Tool 中，执行函数交给 ToolRegistry 登记。这样调整
参数说明时不必改动主循环。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from litagent.config import Settings
from litagent.config import settings as default_settings
from litagent.index.store import Store
from litagent.schema import summary_quality


@dataclass
class Tool:
    """一个可被 Agent 调用的工具，声明与实现分开放，改描述不必动执行逻辑。"""

    name:str  #名字就是模型看到的函数名，英文小写下划线
    description:str
    parameters:Dict[str,Any]
    handler:Callable[...,Any]

    def to_schema(self)->Dict[str,Any]:
        """OpenAI function-calling 风格的 schema，也用于纯文本注入提示词，两边共用一份声明。"""
        return{
            "name":self.name,  #取名字字段
            "description":self.description,
            "parameters":self.parameters,
        }

    #给模型读的文本形态
    def to_prompt_text(self)->str:
        """把工具描述成注入系统提示词的文本块：名字、描述、逐条参数，模型据此选工具和填参。"""
        properties=self.parameters.get("properties") or{}
        required=set(self.parameters.get("required") or[])  #必填项做成集合
        lines=["### %s"%self.name,self.description.strip(),"参数："]
        if not properties:
            lines.append("  （无）")
        for name,spec in properties.items():
            flag="必填" if name in required else "可选"  #这一行在参数清单里就标必填，其余标可选
            type_name=spec.get("type","string")
            if type_name=="array":
                type_name="array<%s>"%(spec.get("items",{}).get("type","string"))
            desc=spec.get("description","")  #描述取不到就给空串，后面拼接照样拼得下去
            default=spec.get("default")
            tail=""
            if default is not None:
                tail="，默认 %s"%json.dumps(default,ensure_ascii=False)
            lines.append("  - %s (%s，%s)：%s%s"%(name,type_name,flag,desc,tail))  #每行一个参数名（类型，必填/可选）：说明，默认值
        return "\n".join(lines)

class ToolRegistry:
    """工具注册表：管登记顺序，并负责把工具导出成 schema、文本和执行入口。"""

    #内部状态：字典存对象，列表存顺序
    def __init__(self)->None:
        self._tools:Dict[str,Tool]={}
        self._order:List[str]=[]

    def register(self,tool:Tool)->None:
        if tool.name not in self._tools:  #新名字才登记顺序
            self._order.append(tool.name)
        self._tools[tool.name]=tool

    def add(self,name:str,description:str,parameters:Dict[str,Any]):
        """装饰器写法：'@registry.add("name", "描述", schema)'，函数体就是工具实现。"""
        def decorator(func:Callable[...,Any])->Callable[...,Any]:
            self.register(Tool(name=name,description=description,
                               parameters=parameters,handler=func))
            return func
        return decorator

    def get(self,name:str)->Optional[Tool]:
        return self._tools.get(name)

    def names(self)->List[str]:
        return list(self._order)

    def __len__(self)->int:
        return len(self._tools)

    #导出给模型
    def schemas(self)->List[Dict[str,Any]]:
        return[self._tools[name].to_schema() for name in self._order]

    def prompt_text(self)->str:
        return "\n\n".join(self._tools[name].to_prompt_text() for name in self._order)  #工具之间空一行

    def execute(self,name:str,arguments:Any)->Any:
        """执行工具。任何异常都被转成 '{"error": ...}' 交给模型自行应对，绝不往外抛。"""
        tool=self._tools.get(name)
        if tool is None:
            return{"error":"未知工具 %r。可用工具：%s"  #名字打错时不抛异常，改把可用工具一并回给模型
                             %(name,", ".join(self._order))}
        if arguments is None:
            arguments={}
        if isinstance(arguments,str):
            try:
                arguments=json.loads(arguments) if arguments.strip() else{}  #空串不当 JSON 解析，直接当没传参数
            except ValueError:
                return{"error":"参数不是合法 JSON：%s"%arguments[:200]}
        if not isinstance(arguments,dict):
            return{"error":"参数必须是 JSON 对象，收到 %s"%type(arguments).__name__}  #参数不是对象时直接退回去

        allowed=set((tool.parameters.get("properties") or{}).keys())
        filtered={k:v for k,v in arguments.items() if k in allowed}  #白名单式过滤：工具没声明的字段一律丢掉
        try:
            return tool.handler(**filtered)
        except TypeError as exc:
            return{"error":"参数不匹配：%s"%exc}  #参数对不上号单独接，回一句参数不匹配
        except Exception as exc:
            return{"error":"%s: %s"%(type(exc).__name__,str(exc)[:300])}

#参数 schema 片段
def _string(description:str,default:Optional[str]=None)->Dict[str,Any]:  #每个参数至少要有一个能读懂的描述，所以描述是必传的
    spec:Dict[str,Any]={"type":"string","description":description}
    if default is not None:
        spec["default"]=default
    return spec

def _integer(description:str,default:Optional[int]=None)->Dict[str,Any]:  #整数单独有个工厂
    spec:Dict[str,Any]={"type":"integer","description":description}
    if default is not None:
        spec["default"]=default
    return spec

def _object(properties:Dict[str,Any],required:Sequence[str]=())->Dict[str,Any]:  #required用序列接，调用处就能写成列表或元组
    return{"type":"object","properties":properties,"required":list(required)}

#工具实现
class LiteratureTools:  #所有工具都挂在同一个类上
    """基于本地语料的文献工具集。"""

    def __init__(self,store:Store,settings:Optional[Settings]=None)->None:
        self.store=store  #构造只存引用，一句检索都不跑
        self.settings=settings or default_settings
        self._searcher:Optional[Any]=None
        self._tracer:Optional[Any]=None

    #惰性构造（避免没用到也要建索引对象）
    @property
    def searcher(self)->Any:
        if self._searcher is None:  #第一次访问才在函数体里导入
            from litagent.retrieve.search import HybridSearcher
            self._searcher=HybridSearcher(self.store,self.settings)
        return self._searcher

    @property
    def tracer(self)->Any:
        if self._tracer is None:
            from litagent.retrieve.trace import QuoteTracer

            self._tracer=QuoteTracer(self.store,self.settings)  #溯源器同理
        return self._tracer

    @staticmethod
    def _paper_brief(doc:Any,*,score:Optional[float]=None,
                     snippet:str="")->Dict[str,Any]:
        item:Dict[str,Any]={
            "doc_id":doc.id,
            "title":doc.title,
            "authors":[a.display for a in doc.authors[:4]],
            "year":doc.year,
            "venue":doc.venue,
            "venue_type":doc.venue_type,
            "doi":doc.doi,
            "discipline_tags":list(doc.discipline_tags),
            "has_fulltext":bool((doc.extra or{}).get("chunk_count")),  #有没有全文由片段数派生
            "retracted":bool(getattr(doc,"retracted",False)),  #是否已撤稿（只读派生，来自 extra）
            "status":str(getattr(doc,"status","active") or "active"),  #文献状态：active/retracted/preprint/erratum
        }
        if score is not None:
            item["score"]=round(float(score),4)
        if snippet:
            item["snippet"]=snippet[:260]  #片段截到 260 字符：够模型判断相不相关
        return item

    def _citation(self,doc:Any)->str:
        try:
            from litagent.cite.gbt7714 import format_gbt7714

            return format_gbt7714(doc.to_citation_dict())  #走to_citation_dict这一步转换
        except Exception:
            return ""

    @staticmethod
    def _summary_status(summary:Any,doc:Any=None)->Tuple[str,List[str]]:
        """返回总结完整度和缺失字段，避免把“只有论断”误当成完整总结。"""
        status,missing=summary_quality(summary,doc)
        labels={
            "complete":"完整",
            "title_limited":"仅含概述（题录级内容）",
            "abstract_limited":"摘要级总结",
            "claims_only":"仅论断",
            "partial":"部分缺失",
        }
        return labels.get(status,status),missing

    #工具实现
    def search_papers(self,query:str,top_k:int=6,
                      section:Optional[str]=None)->Dict[str,Any]:
        """按主题检索本地语料，返回相关文献（已按文献聚合）。"""
        results,report=self.searcher.search_documents(
            query,top_k=max(1,min(int(top_k),20))  #上下界都夹一遍
        )
        papers=[]
        for doc,score,hits in results:
            if section:
                filtered=[h for h in hits if h.section==section]  #指定章节就逐条过筛
                if not filtered:
                    continue
                hits=filtered
            papers.append(self._paper_brief(doc,score=score,
                                            snippet=hits[0].snippet(240)))
        return{
            "query":query,
            "count":len(papers),  #回显实际返回条数
            "papers":papers,
            "routes":report.routes_used,
            "note":"" if papers else "本地语料中没有匹配的文献。",
        }

    def trace_quote(self,quote:str,allow_online:bool=False)->Dict[str,Any]:
        """找出某段话出自哪篇论文，并给出 GB/T 7714 引用。"""
        result,report=self.tracer.trace(quote,allow_online=allow_online)  #允许联网由调用方按需打开：默认只查本地语料
        payload:Dict[str,Any]={
            "found":result.found,
            "level":result.level,
            "confidence":round(result.confidence,4),  #置信度同样取到小数点后四位：既方便模型比大小
            "decision_basis":result.decision_basis,
            "supporting_signals":dict(result.supporting_signals),
            "note":result.note,
            "matched_text":result.matched_text[:600],
            "context":{
                "before":result.context_before[-300:],  #上文取末尾 300 字：和命中处贴着
                "after":result.context_after[:300],
            },
        }
        if result.hit is not None and result.hit.doc is not None:  #只有真拿到了文献对象才补出处
            doc=result.hit.doc
            payload["source"]=self._paper_brief(doc,score=result.hit.score)
            payload["source"]["section"]=result.hit.section
            payload["source"]["page"]=result.hit.page  #页码同样是溯源才有的字段
            payload["citation"]=self._citation(doc)
        if result.candidates:
            payload["other_candidates"]=[
                {"doc_id":h.doc_id,  #非首选命中也给标识
                 "title":h.doc.title if h.doc else "",
                 "score":round(h.score,4)}
                for h in result.candidates[:3]
            ]
        if result.online_candidates:  #联网结果与本地结果分开放在另一个字段里
            payload["online_candidates"]=[
                {"provider":item.get("provider"),
                 "source":item.get("source"),
                 "source_tier":item.get("source_tier"),
                 "is_local_evidence":item.get("is_local_evidence",False),
                 "title":item.get("title"),"year":item.get("year"),
                 "venue":item.get("venue"),"doi":item.get("doi"),
                 "url":item.get("url"),
                 "citation":item.get("citation")}  #同样取前四条：联网结果只是给模型一个去处
                for item in result.online_candidates[:4]
            ]
        payload["diagnostics"]=report.summary()
        return payload

    #取一篇文献的题录
    def get_paper_detail(self,doc_id:str,include_chunks:bool=False)->Dict[str,Any]:
        """取一篇文献的完整题录与摘要。"""
        doc=self.store.get_document(doc_id,with_fulltext=False)
        if doc is None:  #取不到就返回一条error观察值而不是抛异常
            return{"error":"找不到文献 %r"%doc_id}
        payload=self._paper_brief(doc)
        payload["full_authors"]=[a.display for a in doc.authors]
        payload["abstract"]=(doc.abstract or "")[:2000]  #摘要截断到 2000 字：再长也读不完
        payload["citation"]=self._citation(doc)
        payload["keywords"]=doc.keywords[:12]
        summary=self.store.get_summary(doc_id)
        if summary is not None and not summary.is_empty():  #is_empty是结构判空
            status,missing=self._summary_status(summary,doc)
            payload["summary"]={
                "tldr":summary.tldr,
                "methods":summary.methods,
                "findings":summary.findings,
                "limitations":summary.limitations,
                "summary_status":status,
                "missing_fields":missing,
            }
        if include_chunks:  #默认不带正文片段：一段正文几百字
            chunks=list(self.store.iter_chunks(doc_id))[:12]
            payload["chunks"]=[
                {"ordinal":c.ordinal,"section":c.section,"page":c.page_start,
                 "text":c.text[:700]}  #每段截到 700 字：要的是让人认出这是哪一段
                for c in chunks
            ]
        return payload

    #取结构化总结
    def get_summary(self,doc_id:str)->Dict[str,Any]:
        """取一篇文献的结构化总结与可引用论断。"""
        summary=self.store.get_summary(doc_id)
        if summary is None or summary.is_empty():  #没有记录、或者记录存在但字段全空
            doc=self.store.get_document(doc_id,with_fulltext=False)
            return{
                "available":False,
                "title":doc.title if doc else "",  #标题取不到就给空串
                "summary_status":"未生成",
                "missing_fields":["tldr","problem","methods","findings","contributions","limitations"],
                "note":"这篇文献还没有结构化总结。可提示用户运行 "
                        "`cli.py summarize --id %s` 生成。"%doc_id,
            }
        doc=self.store.get_document(doc_id,with_fulltext=False)  #未命中的分支已经早退
        status,missing=self._summary_status(summary,doc)
        return{
            "available":True,
            "doc_id":doc_id,
            "title":doc.title if doc else "",  #标题兜成空串而不是None
            "summary_status":status,
            "missing_fields":missing,
            "tldr":summary.tldr,
            "problem":summary.problem,
            "methods":summary.methods,
            "findings":summary.findings,
            "contributions":summary.contributions,
            "limitations":summary.limitations,
            "keywords":summary.keywords,
            "claims":[  #论断最多给 12 条，按数据库给的顺序取
                {"text":c.text,"section":c.section,"page":c.page}
                for c in summary.claims[:12]
            ],
            "citation":self._citation(doc) if doc else "",  #有正文才会有引用串
        }

    def format_citation(self,doc_id:str,style:str="gbt7714")->Dict[str,Any]:
        """按指定格式输出引用。'style'可选 gbt7714、bibtex、ris，传入其它值时退回 gbt7714。"""
        doc=self.store.get_document(doc_id,with_fulltext=False)
        if doc is None:
            doc=self.store.find_by_doi(doc_id)
        if doc is None:  #两种查法都没命中才报错
            return{"error":"找不到文献 %r"%doc_id}
        try:
            from litagent.cite.gbt7714 import format_all

            formats=format_all(doc.to_citation_dict())
        except Exception as exc:
            return{"error":"引用格式化失败：%s"%exc}
        key=style if style in formats else "gbt7714"
        return{
            "doc_id":doc.id,
            "title":doc.title,
            "style":key,
            "citation":formats.get(key,""),
            "all_formats":formats,
        }

    #推荐文献
    def recommend_papers(self,topic:str,top_k:int=5,
                         year_from:Optional[int]=None,
                         exclude_ids:Optional[List[str]]=None)->Dict[str,Any]:
        """按研究主题推荐文献，可按年份过滤。"""
        results,_=self.searcher.search_documents(  #多取三倍候选：后面要按年份和已读清单做过滤
            topic,top_k=max(1,min(int(top_k)*3,40))
        )
        excluded=set(exclude_ids or[])
        papers=[]  #结果预置成列表
        for doc,score,hits in results:
            if doc.id in excluded:
                continue
            if year_from and doc.year and doc.year<int(year_from):  #年份过滤分两段判断：没给下界就不过滤
                continue
            papers.append(self._paper_brief(doc,score=score,
                                            snippet=hits[0].snippet(200)))
            if len(papers)>=int(top_k):  #攒够top_k就早退
                break
        return{"topic":topic,"count":len(papers),"papers":papers,
                "note":"" if papers else "没有找到符合条件的文献。"}

    #语料概况
    def corpus_stats(self)->Dict[str,Any]:
        """给出本地语料概况：文献数、有全文的篇数、覆盖的年份与来源。"""
        stats=self.store.stats()
        return{
            "documents":stats.get("documents",0),  #所有计数都兜 0
            "documents_with_fulltext":stats.get("documents_with_fulltext",0),
            "chunks":stats.get("chunks",0),
            "summaries":stats.get("summaries",0),
            "routes":["bm25","phrase"],  #当前的作品只保留这两路召回：BM25 关键词 + 精确 n-gram 指纹
            "by_source":stats.get("by_source",{}),
            "by_venue_type":stats.get("by_venue_type",{}),
            "by_year":stats.get("by_year",{}),
            "note":("语料较少时检索效果有限，可先运行 "
                     "`cli.py fetch` 扩充。"),
        }

    #文献真实性核验
    def verify_citation(self,doc_id:Optional[str]=None,title:Optional[str]=None,
                        authors:Optional[str]=None,year:Optional[str]=None,
                        doi:Optional[str]=None)->Dict[str,Any]:
        """核验一篇文献的真实性与撤稿状态，返回带出处的证据。

        结论由确定性代码计算（Verifier），本工具只回传 verdict + evidence。
        大模型只能复述这些字段，不得自行判断。
        """
        from litagent.schema import Document, normalize_doi, parse_authors
        from litagent.verify import Verifier

        doc=None
        if doc_id:
            doc=self.store.get_document(doc_id,with_fulltext=False)  #先按库内 ID 取
        if doc is None:
            if not (title or "").strip():  #没有标题就无从核验
                return{"error":"缺少可核验的信息：请提供 doc_id 或 title。"}
            doc=Document(  #用题录临时构造一个 Document 交给核验器
                id=Document.make_id("verify",None,doi,(title or "").strip()),
                title=(title or "").strip(),
                authors=parse_authors([authors] if authors else[]),
                year=int(year) if str(year or "").strip().isdigit() else None,
                doi=normalize_doi(doi),
            )
        if doc is None or not (doc.title or "").strip():
            return{"error":"缺少可核验的信息：请提供 doc_id 或 title。"}

        try:
            result=Verifier(self.settings).verify(doc)  #核验逻辑全在 Verifier 里
        except Exception as exc:  # noqa: BLE001
            return{"error":"核验失败：%s: %s"%(type(exc).__name__,str(exc)[:160])}

        payload=result.to_compact_dict()  #体积受控，避免撞上 observation 截断
        payload["title"]=doc.title
        payload["doi"]=doc.doi
        payload["doc_id"]=doc.id
        return payload

    #联网抓取
    DOWNLOAD_BUDGET=90.0  #补下全文的时间预算（秒）：死链拖时间时到点收手，剩下的留到下次

    def fetch_online(self,query:str,limit:int=5,
                     source:str="openalex",preview:bool=True)->Dict[str,Any]:
        """联网检索文献。默认只预览候选，不给库；预览参数显式为假时才入库。"""
        limit=max(1,min(int(limit),20))  #入参先归一化：模型可能传字符串或负数
        if preview:
            try:
                from litagent.fetch.base import get_source

                source_obj=get_source(source,self.settings)
                try:
                    documents=list(source_obj.search(query,limit=limit))
                finally:
                    source_obj.close()
                papers=[]
                # 自动核验：只对前 5 篇做，并行、每篇单篇上限 8 秒。核验结果内嵌进每篇的 verification 字段。
                _VERIFY_MAX=5
                _VERIFY_PER_BUDGET=8.0
                verify_results={}
                if documents:
                    try:
                        from concurrent.futures import ThreadPoolExecutor, as_completed

                        from litagent.verify import Verifier

                        verifier=Verifier(self.settings)
                        n_verify=min(_VERIFY_MAX,len(documents))
                        with ThreadPoolExecutor(max_workers=n_verify) as pool:
                            futures={
                                pool.submit(verifier.verify,documents[i],budget_seconds=_VERIFY_PER_BUDGET):i
                                for i in range(n_verify)
                            }
                            for future in as_completed(futures):
                                idx=futures[future]
                                try:
                                    verify_results[idx]=future.result().to_compact_dict()
                                except Exception:
                                    verify_results[idx]={
                                        "verdict":"unverified","verdict_label":"未核验",
                                        "confidence":0.0,"note":"核验失败或超时","evidence":[],
                                    }
                    except Exception:
                        verify_results={}  #核验整体失败不影响预览
                for idx,doc in enumerate(documents):
                    item=self._paper_brief(doc)
                    item["url"]=doc.url
                    item["is_oa"]=bool(doc.is_oa)
                    item["citation"]=self._citation(doc)
                    if idx in verify_results:
                        item["verification"]=verify_results[idx]  #检索后自动附带的真实性核验结论
                    papers.append(item)
                return{
                    "query":query,
                    "source":source,
                    "preview":True,
                    "count":len(papers),
                    "papers":papers,
                    "note":("仅预览，未写入本地库。确认这些候选后，调用时传 preview=false 才会入库。"),
                }
            except Exception as exc:
                return{
                    "query":query,
                    "source":source,
                    "preview":True,
                    "count":0,
                    "papers":[],
                    "error":"%s: %s"%(type(exc).__name__,str(exc)[:200]),
                    "note":"联网预览失败，未改动本地库。",
                }

        from litagent.fetch.runner import FetchRunner

        runner=FetchRunner(self.store,self.settings)
        known={doc.id for doc in self.store.iter_documents()}
        report=runner.fetch_online(source,[query],limit_per_query=limit,
                                     archive=True)
        fresh=[doc.id for doc in self.store.iter_documents() if doc.id not in known]
        #联网源只给题录，正文得靠这一步补：下到 PDF 才能溯源到句子，下不到就只能拿摘要甚至标题建片段
        pdfs=0
        if fresh:
            try:
                download=runner.download_pdfs(fresh,limit=len(fresh),
                                              budget_seconds=self.DOWNLOAD_BUDGET)
                pdfs=download.pdfs_downloaded
            except Exception as exc:
                report.errors.append("全文下载未完成：%s"%str(exc)[:120])

        #后台补完下游步骤
        cleaned=None
        index_error=""
        if report.new:
            try:
                from litagent.clean.pipeline import CleanPipeline
                from litagent.index.builder import IndexBuilder

                cleaned=CleanPipeline(self.store,self.settings).run(redo=False)  #只处理还没清洗过的文献
                IndexBuilder(self.store,self.settings).build_lexical()
            except Exception as exc:
                index_error="%s: %s"%(type(exc).__name__,str(exc)[:160])

        if not report.new:
            note="没有新增文献。"
        elif index_error:
            note=("新入库的文献已抓取，但后台清洗/建索引失败（%s），"
                  "需要人工运行 cli.py clean 与 cli.py index。"%index_error)
        else:
            note=("新入库的 %d 篇已自动完成全文补下、清洗与建索引，现在可以直接用 search_papers 检索到"
                  "（本次下到全文 %d 篇，新增 %d 个正文片段）。"
            %(report.new,pdfs,cleaned.chunks_created if cleaned else 0))
            if cleaned and cleaned.from_abstract_only:
                note+=("另有 %d 篇没有可下载的开放获取全文，只能按摘要或标题建片段："
                       "可以检索、可以引用，但溯源不到具体句子。"
                       %cleaned.from_abstract_only)

        #语料变了，检索器要重建内部统计
        if self._searcher is not None:
            self._searcher.refresh()
        return{
            "query":query,
            "source":source,
            "preview":False,
            "new":report.new,
            "merged":report.merged,
            "errors":report.errors[:3],  #错误只回传前三条，全量回传会把观察值占满
            "ready":bool(report.new and not index_error),
            "pdfs_downloaded":pdfs,  #本轮下到全文的篇数
            "abstract_only":(cleaned.from_abstract_only if cleaned else 0),  #只有摘要或标题、溯源不到句子的篇数
            "chunks_created":(cleaned.chunks_created if cleaned else 0),
            "index_error":index_error,
            "note":note,
        }

    def build_registry(self)->ToolRegistry:  #构建工具注册表
        registry=ToolRegistry()

        registry.register(Tool(
            name="search_papers",
            description=("在本地文献库中按主题或关键词检索相关论文。"
                         "当用户想找某个方向的文献时先用这个工具。"
                         "返回按文献聚合的结果，每篇含标题、作者、年份、出处、摘要片段。"),
            parameters=_object({
                "query":_string("检索词，中英文均可，例如 'graph neural network recommendation'"),  #检索词写清中英文都行，并给一个示例
                "top_k":_integer("返回文献数量，1~20",6),
                "section":_string("只检索论文的某个章节，可选值："
                                   "abstract/introduction/method/experiment/"
                                   "results/discussion/conclusion",None),
            },["query"]),
            handler=self.search_papers,
        ))

        #引文溯源
        registry.register(Tool(
            name="trace_quote",
            description=("【溯源】给出一段话，找出它出自本地库中的哪篇论文，"
                         "并返回 GB/T 7714-2015 格式的引用。"
                         "当用户粘贴一段文字问「这段话出自哪里」「这句是谁说的」时用这个。"
                         "**返回结果里 found=false 表示没找到，你必须如实告诉用户没找到，"
                         "绝不能自己编造一篇文献**。"),
            parameters=_object({
                "quote":_string("要溯源的原文本，越长越准。建议至少一整句"),  #检索词说明里强调越长越准
                "allow_online":{"type":"boolean",
                                 "description":"本地找不到时是否联网检索候选",
                                 "default":False},
            },["quote"]),
            handler=self.trace_quote,
        ))

        #查看文献详情
        registry.register(Tool(
            name="get_paper_detail",
            description="查看某篇文献的完整题录与摘要（需要 doc_id）。",
            parameters=_object({
                "doc_id":_string("文献 ID，形如 doi:10.1145/xxxx 或 openalex:W123"),
                "include_chunks":{"type":"boolean",
                                   "description":"是否附带正文片段",
                                   "default":False},
            },["doc_id"]),
            handler=self.get_paper_detail,
        ))

        #查看结构化总结
        registry.register(Tool(
            name="get_summary",
            description=("取一篇文献的结构化总结（研究问题/方法/发现/贡献/局限）"
                         "以及**可直接引用的论断列表**。"
                         "当用户想知道某篇论文讲了什么、或者需要找论据时用这个。"),
            parameters=_object({
                "doc_id":_string("文献 ID"),  #只要一个编号，参数表就一行
            },["doc_id"]),
            handler=self.get_summary,
        ))

        #输出引用格式
        registry.register(Tool(
            name="format_citation",
            description="按指定格式输出一条参考文献著录。",
            parameters=_object({
                "doc_id":_string("文献 ID 或 DOI"),  #编号允许传 DOI
                "style":{"type":"string","enum":["gbt7714","bibtex","ris"],
                          "description":"引用格式","default":"gbt7714"},
            },["doc_id"]),
            handler=self.format_citation,
        ))

        #主题推荐
        registry.register(Tool(
            name="recommend_papers",
            description=("按研究主题推荐文献。与 search_papers 的区别是它会按"
                         "年份过滤并跳过用户已看过的文献，更适合「给我推荐几篇」这类请求。"),  #与检索的区别要写进描述：按年份过滤并跳过看过的
            parameters=_object({
                "topic":_string("研究主题"),
                "top_k":_integer("推荐数量",5),
                "year_from":_integer("只要这个年份之后的文献",None),  #年份下界缺省为不限制
                "exclude_ids":{"type":"array","items":{"type":"string"},
                                "description":"要排除的文献 ID 列表（已推荐过的）"},
            },["topic"]),
            handler=self.recommend_papers,
        ))

        #查看语料概况
        registry.register(Tool(
            name="corpus_stats",
            description=("查看本地文献库的概况：收录了多少篇、多少篇有全文、"
                         "覆盖哪些年份。当用户问「你这里有什么」或检索结果为空需要解释原因时用。"),
            parameters=_object({}),  #不需要任何参数，空对象即可
            handler=self.corpus_stats,
        ))

        #文献真实性核验
        registry.register(Tool(
            name="verify_citation",
            description=("【核验】核验一篇文献的真实性与撤稿状态。"
                         "当用户问「这篇文献是真的吗」「是否被撤稿」「这条参考文献靠不靠谱」时用这个。"
                         "**核验结论只能来自本工具返回的 verdict 与 evidence 字段**："
                         "evidence 里没有标记撤稿，就绝不能说「已撤稿」；"
                         "返回「无法确认」时如实说明「这几个库未收录、无法确认」，"
                         "绝不能替用户下「伪造」的结论。"),
            parameters=_object({
                "doc_id":_string("库内文献 ID（形如 doi:10.1145/xxxx 或 openalex:W123）",None),
                "title":_string("文献标题（doc_id 为空时必填）",None),
                "authors":_string("作者，逗号分隔",None),
                "year":_string("出版年份",None),
                "doi":_string("DOI",None),
            },[]),  #doc_id 与 title 二选一，故不设 required
            handler=self.verify_citation,
        ))

        #联网扩充语料
        registry.register(Tool(
            name="fetch_online",
            description=("从开放学术数据库（OpenAlex/Crossref/arXiv/Semantic Scholar/OpenCLI）联网检索并"
                         "预览或加入本地库。默认只预览、不写库；用户明确确认后才传 preview=false。"  #同一句里说清定位
                         "确认入库后会自动补下开放获取全文、清洗并建索引，返回时即可用 search_papers 检索到。"
                         "没有开放获取全文的文献只能按摘要建片段。"),
            parameters=_object({
                "query":_string("检索词"),
                "limit":_integer("抓取数量，1~20",5),
                "source":{"type":"string",
                           "enum":["openalex","crossref","arxiv","semanticscholar","opencli"],  #数据源做成枚举
                           "description":"数据源","default":"openalex"},
                "preview":{"type":"boolean",
                            "description":"默认 true，只返回候选、不写库；用户确认后才传 false",
                            "default":True},
            },["query"]),
            handler=self.fetch_online,
        ))

        return registry

__all__=["Tool","ToolRegistry","LiteratureTools"]
