"""文献检索、引文溯源和 Agent 对话的 Streamlit 网页"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any,Dict,List,Optional
from urllib.parse import urlparse

PROJECT_ROOT=Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0,str(PROJECT_ROOT))

import streamlit as st

from litagent.config import ensure_deps_on_path,get_settings

settings=get_settings()
ensure_deps_on_path(settings.deps_dir)

from litagent.index.store import Store
from litagent.schema import summary_quality

st.set_page_config(
    page_title="lit-agent 文献助手",
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="expanded",
)

#控件铺满宽度：Streamlit 1.49 起把 use_container_width 换成了 width="stretch"，
#旧参数虽然还能用但每次都会刷弃用警告，以后还会直接删掉。这里按版本挑参数，
#装的是哪个版本都不会报错，控制台也清净。
def stretch_kwargs()->Dict[str,Any]:
    try:
        major,minor=[int(x) for x in st.__version__.split(".")[:2]]
    except Exception:  # noqa: BLE001 版本号读不出来就按旧写法走
        return{"use_container_width":True}
    return{"width":"stretch"} if(major,minor)>=(1,49) else{"use_container_width":True}

#资源缓存
@st.cache_resource(show_spinner=False)
def get_store()->Store:
    store=Store(settings.db_path)
    store.init_schema()
    return store

@st.cache_resource(show_spinner=False)
def get_tracer()->Any:
    from litagent.retrieve.trace import QuoteTracer

    return QuoteTracer(get_store(),settings)

#侧边栏：语料概况 + 能力状态
def render_sidebar()->None:
    with st.sidebar:
        st.title("📚 lit-agent")
        st.caption("面向论文写作的文献检索助手")
        try:
            stats=get_store().stats()
            col1,col2=st.columns(2)
            col1.metric("文献",stats.get("documents",0))
            col2.metric("含全文",stats.get("documents_with_fulltext",0))
            col1.metric("片段",stats.get("chunks",0))
            col2.metric("结构化总结",stats.get("summaries",0))
        except Exception as exc:  #noqa: BLE001
            st.error("读取索引失败：%s"%exc)

        st.divider()
        st.write("🧠 大模型："+("已配置" if settings.has_llm else "❌ 未配置"))
        if not settings.has_llm:
            st.info("未配置 `LIT_LLM_API_KEY`：**对话**不可用，但**溯源**完全正常。")
        st.caption("本地召回：BM25 + 精确 n-gram 指纹")
        st.caption("联网时自动补充 OpenCLI 浏览器候选")

#公共渲染：文献卡片
#数据源名的显示写法：联网候选来自四个库，卡片上要用正式写法标清楚是哪一个
SOURCE_LABELS={"openalex":"OpenAlex","crossref":"Crossref",
               "arxiv":"arXiv","semanticscholar":"Semantic Scholar",
               "opencli":"OpenCLI",
               "opencli:google-scholar":"OpenCLI · Google Scholar",
               "opencli:cnki":"OpenCLI · CNKI",
               "opencli:wanfang":"OpenCLI · 万方",
               "opencli:baidu-scholar":"OpenCLI · 百度学术"}

def _summary_status_label(
    summary:Any,doc:Any=None,*,content_chars:Optional[int]=None
)->str:
    """给界面展示总结完整度，避免把只有论断的旧记录看成完整总结。"""
    status,missing=summary_quality(
        summary,doc,content_chars=content_chars
    )
    labels={
        "complete":"完整",
        "title_limited":"仅含概述（题录级内容）",
        "abstract_limited":"摘要级总结（部分结构化字段不可得）",
        "short_content_limited":"输入过短（仅可概述）",
        "claims_only":"仅论断（六项结构化字段为空）",
    }
    if status in labels:
        return labels[status]
    field_labels={
        "tldr":"一句话","problem":"研究问题","methods":"方法",
        "findings":"主要发现","contributions":"贡献","limitations":"局限",
    }
    return "部分缺失："+"、".join(field_labels.get(field,field) for field in missing)


def _candidate_link(item:Dict[str,Any])->str:
    """返回可安全展示的原文或题录链接。"""
    raw=str(item.get("url") or "").strip()
    if raw:
        parsed=urlparse(raw)
        if parsed.scheme in("http","https"):
            return raw
    doi=str(item.get("doi") or "").strip()
    if doi:
        return "https://doi.org/"+doi.removeprefix("https://doi.org/").strip("/")
    return ""

def render_paper_card(item:Dict[str,Any],index:Optional[int]=None,
                      expanded:bool=False)->None:
    """渲染一张文献卡片。同一页有多张卡片时必须给不同的 index：它既当标题编号，也参与组件 key。"""
    prefix="%d. "%index if index else ""
    with st.expander("%s%s"%(prefix,item.get("title") or "(无标题)"),
                     expanded=expanded):
        meta_bits=[]
        if item.get("authors"):
            authors=item["authors"]
            shown=", ".join(authors[:4]) if isinstance(authors,list) else str(authors)
            if isinstance(authors,list) and len(authors)>4:
                shown+=" 等"
            meta_bits.append(shown)
        if item.get("year"):
            meta_bits.append(str(item["year"]))
        if item.get("venue"):
            meta_bits.append(str(item["venue"]))
        if meta_bits:
            st.caption(" · ".join(meta_bits))
        if item.get("source"):
            st.caption("来源数据库：%s"%SOURCE_LABELS.get(item["source"],item["source"]))
        tags=item.get("discipline_tags") or[]
        if tags:
            st.caption("学科标签："+" · ".join(str(tag) for tag in tags))
        if item.get("source_tier")=="browser_supplement":
            st.caption("来源层级：浏览器补充（本地库未收录）")
        if item.get("doi"):
            st.caption("DOI: %s"%item["doi"])
        link=_candidate_link(item)
        if link:
            st.link_button("打开原文 / 来源页",link)
        if item.get("snippet"):
            st.write(item["snippet"])
        if item.get("citation"):
            tag=item.get("doc_id") or item.get("doi") or item.get("title") or "item"  #联网候选没有 doc_id，靠 DOI 或标题兜底，避免多张卡片用同一个 key
            st.text_area("GB/T 7714-2015 引用",item["citation"],height=80,
                         key="cite_%s_%s"%(tag,index))

#页签 1：与 Agent 对话
def tab_chat()->None:
    st.header("💬 与 Agent 对话")
    st.caption("用自然语言描述你的需求，Agent 会自己决定检索、溯源、总结还是推荐。")

    if not settings.has_llm:
        st.warning("未配置大模型 API Key，对话功能不可用。\n\n"
                   "在项目根目录的 `.env` 中写入 `LIT_LLM_API_KEY=sk-...` 后重启。\n\n"
                   "申请地址：https://platform.deepseek.com/\n\n"
                   "配好后建议先在命令行跑一次 `cli.py doctor --probe`，"
                   "确认 Key 真的能用（而不只是填上了）。")
        return

    st.session_state.setdefault("chat_history",[])
    st.session_state.setdefault("chat_display",[])

    for turn in st.session_state.chat_display:
        with st.chat_message(turn["role"]):
            st.markdown(turn["content"])
            if turn.get("steps"):
                with st.expander("Agent 的执行过程（%d 步）"%len(turn["steps"])):
                    for step in turn["steps"]:
                        st.markdown("**第 %d 轮**"%step["round"])
                        if step.get("thought"):
                            st.caption("思考：%s"%step["thought"])
                        if step.get("tool"):
                            st.code("%s(%s)"%(step["tool"],
                                                json.dumps(step["arguments"],
                                                           ensure_ascii=False)),
                                    language="json")
                        if step.get("observation"):
                            st.text(step["observation"][:800])

    if not st.session_state.chat_display:
        st.write("**试试这些问题：**")
        examples=[
            "帮我找几篇用图神经网络做推荐系统的论文",
            "哪些论文讨论了推荐系统的冷启动问题？",
            "你这边的语料库收录了多少篇文献？",
        ]
        for col,example in zip(st.columns(len(examples)),examples):
            if col.button(example,key="ex_"+example,**stretch_kwargs()):
                st.session_state.pending_question=example
                st.rerun()

    question=st.chat_input("问点什么，例如：帮我找几篇 GNN 推荐系统的论文")
    if not question:
        question=st.session_state.pop("pending_question",None)
    if not question:
        return

    st.session_state.chat_display.append({"role":"user","content":question})
    with st.chat_message("user"):
        st.markdown(question)

    step_log:List[Dict[str,Any]]=[]
    with st.chat_message("assistant"):
        status=st.status("Agent 正在思考…",expanded=True)

        def on_step(step:Any)->None:
            step_log.append(step.to_dict())
            if step.tool:
                status.write("🔧 调用 `%s`"%step.tool)
            if step.error:
                status.write("⚠️ %s"%step.error[:160])

        try:
            from litagent.agent.loop import LiteratureAgent

            agent=LiteratureAgent(get_store(),settings)
            started=time.monotonic()
            try:
                result=agent.run(question,
                                   history=st.session_state.chat_history[-8:],
                                   on_step=on_step)
            finally:
                agent.close()
            elapsed=time.monotonic()-started
            status.update(
                label="完成（%d 轮 · %.1f 秒 · 输入 %d / 输出 %d token）"
                      %(result.rounds,elapsed,result.prompt_tokens,
                         result.completion_tokens),
                state="complete",expanded=False,
            )
            st.markdown(result.answer)
            get_store().record_query_history(
                "agent",question,
                found=result.stopped_reason=="final_answer",
                level=result.stopped_reason,
                result_count=result.rounds,
                metadata={
                    "elapsed_seconds":round(elapsed,3),
                    "answer":result.answer[:1200],
                    "tool_steps":len(step_log),
                },
            )
            if result.stopped_reason!="final_answer":
                st.caption("停止原因：%s"%result.stopped_reason)
        except Exception as exc:  #noqa: BLE001
            status.update(label="失败",state="error")
            st.error("调用失败：%s: %s"%(type(exc).__name__,exc))
            return

    st.session_state.chat_history.append({"role":"user","content":question})
    st.session_state.chat_history.append({"role":"assistant","content":result.answer})
    #只保留最近 8 轮，控制上下文长度与费用
    st.session_state.chat_history=st.session_state.chat_history[-16:]
    st.session_state.chat_display.append({
        "role":"assistant","content":result.answer,"steps":step_log,
    })

    if st.button("🗑 清空对话"):
        st.session_state.chat_history=[]
        st.session_state.chat_display=[]
        st.rerun()

#页签 2：溯源
def tab_trace()->None:
    st.header("🔍 溯源：这段话出自哪里")
    st.caption("粘贴一段文字，找出它在本地语料中的出处，并生成 GB/T 7714-2015 引用。")

    pending_load=st.session_state.pop("pending_trace_load",None)
    if pending_load:
        st.session_state["trace_input"]=pending_load.get("query","")
        st.session_state["trace_source_hint"]=pending_load.get("source_hint","")
        st.session_state["trace_online"]=bool(pending_load.get("allow_online"))

    quote=st.text_area(
        "待溯源的文本",
        height=200,
        placeholder="把你看到的一段话粘贴到这里。越长越准——建议至少一整句，"
                    "最好一整段。",
        key="trace_input",
    )
    source_hint=st.text_input(
        "DOI / 含 DOI 的来源链接（选填）",
        placeholder="例如 10.1360/tb-2024-0376 或 https://doi.org/...",
        key="trace_source_hint",
        help="系统会优先按 DOI 解析题录；普通知网文章页仍用正文执行联网检索。",
    )

    col1,col2,col3=st.columns([1,1,2])
    with col1:
        run=st.button("开始溯源",type="primary",**stretch_kwargs())
    with col2:
        allow_online=st.checkbox("本地找不到时联网",value=False,key="trace_online",
                                   help="联网结果会明确标注「本地库未收录」，需自行核对")
    with col3:
        st.caption("提示：只有**已经收录进本地库**的文献才可能被溯源到。")

    if not run or not quote.strip():
        return

    tracer=get_tracer()
    with st.spinner("正在比对 n-gram 指纹与 BM25…"):
        result,report=tracer.trace(
            quote,allow_online=allow_online,online_interactive=True,
            source_hint=source_hint,
        )
    history_candidates=[]
    if result.hit is not None and result.hit.doc is not None:
        history_candidates.append({
            "title":result.hit.doc.title,
            "url":_candidate_link(result.hit.doc.to_dict()),
            "relation":"local",
        })
    history_candidates.extend({
        "title":item.get("title",""),
        "url":item.get("url") or _candidate_link(item),
        "relation":"online",
    } for item in result.online_candidates[:8])
    get_store().record_query_history(
        "trace",quote,
        found=result.found,
        level=result.level,
        result_count=(1 if result.hit is not None else 0)+len(result.online_candidates),
        metadata={
            "allow_online":bool(allow_online),
            "source_hint":source_hint,
            "confidence":result.confidence,
            "candidates":history_candidates,
        },
    )

    if result.found:
        st.success("✅ 找到出处（置信度 %.0f%%，级别 %s）"
                   %(result.confidence*100,result.level))
    elif result.level=="local_weak":
        st.warning("⚠️ 只有部分文本重合，不足以确认出处。**请勿直接据此引用。**")
    elif result.level=="local_semantic":
        st.warning(
            "⚠️ 精确指纹未命中，以下为向量召回的语义相关候选。"
            "**向量相似不能证明原文出处，请勿直接据此引用。**"
        )
    else:
        st.error("❌ 本地语料中没有找到这段话的出处。")

    st.caption(report.summary())
    if report.online_queried:
        with st.expander("联网检索详情",expanded=False):
            if report.online_error:
                st.write(report.online_error)
            else:
                st.write("各来源未报告错误。")
            if report.online_filtered_cross_language:
                st.write(
                    "已过滤 %d 条与查询语种不一致的候选。"
                    %report.online_filtered_cross_language
                )
            if report.online_filtered_low_relevance:
                st.write(
                    "已过滤 %d 条弱相关候选。"
                    %report.online_filtered_low_relevance
                )
    if report.config_warning:
        st.warning(report.config_warning)

    if result.hit is not None:
        hit=result.hit
        doc=hit.doc
        if doc is not None:
            st.subheader(
                "语义相关文献（非原文证据）"
                if result.level=="local_semantic" else "匹配到的文献"
            )
            col1,col2=st.columns([2,1])
            with col1:
                st.markdown("**%s**"%doc.title)
                st.caption("学科标签："+" · ".join(doc.discipline_tags or["综合类"]))
                if doc.authors:
                    st.write("作者："+", ".join(a.display for a in doc.authors[:6]))
                st.write("出处：%s %s"%(doc.venue or "(未知)",doc.year or ""))
                if doc.doi:
                    st.write("DOI：%s"%doc.doi)
            with col2:
                if result.level=="local_semantic":
                    st.metric(
                        "语义相似度",
                        "%.0f%%"%(float(
                            result.supporting_signals.get(
                                "vector_similarity",hit.score
                            )
                        )*100),
                    )
                else:
                    st.metric("置信度","%.0f%%"%(result.confidence*100))
                st.metric("所在章节",hit.section)
                if hit.page:
                    st.metric("PDF 页码","第 %d 页"%hit.page)

            citation=tracer._safe_citation(doc)
            if citation:
                st.text_area("GB/T 7714-2015 引用（可直接复制）",citation,
                             height=80,key="trace_citation")
    if result.online_candidates:
        if result.hit is not None:
            st.divider()
        st.subheader("联网候选（本地库未收录，请自行核实）")
        for rank,item in enumerate(result.online_candidates,start=1):
            render_paper_card(item,index=rank,expanded=False)

    if result.matched_text and(result.context_before or result.context_after):
        st.subheader("原文上下文")
        st.markdown(
            "…%s**【%s】**%s…"
            %(result.context_before[-300:],result.matched_text,
               result.context_after[:300])
        )

    if result.note:
        st.info(result.note)

    if result.candidates:
        with st.expander("其他候选（置信度较低）"):
            for rank,hit in enumerate(result.candidates[:5],start=1):
                doc=hit.doc
                st.write("%d. %.3f ｜ %s"%(rank,hit.score,
                                             doc.title if doc else hit.doc_id))

#缓存刷新：删除文献后必须清掉
def refresh_caches()->None:
    get_store.clear()
    get_tracer.clear()

def _remove_pdf(store:Any,doc_id:str)->int:
    """删除该文献已下载的 PDF 文件。只允许删项目目录以内的文件。"""
    doc=store.get_document(doc_id,with_fulltext=False)
    raw=getattr(doc,"pdf_path",None) if doc is not None else None
    if not raw:
        return 0
    try:
        target=Path(raw).resolve()
        if settings.project_root.resolve() not in target.parents:
            return 0
        if target.is_file():
            target.unlink()
            return 1
    except Exception:
        return 0
    return 0

#页签 3：文献库（查看 + 删除）
def tab_docs()->None:
    st.header("📚 文献库")
    st.caption("库里全部文献都在这里。内容级别为「全文」的才能溯源到句子；"
               "「摘要」「仅标题」表示这篇没有下到开放获取全文。")
    st.caption("数据库：%s"%settings.db_path)

    store=get_store()
    try:
        rows=[dict(r) for r in store.connect().execute(
            "SELECT d.id, d.title, d.year, d.venue, d.source, d.doi,"
            " (SELECT COUNT(*) FROM chunks c WHERE c.doc_id=d.id) AS nchunk,"
            " CASE WHEN EXISTS(SELECT 1 FROM chunks c WHERE c.doc_id=d.id"
            "                  AND c.section NOT IN ('abstract','title')) THEN '全文'"
            "      WHEN EXISTS(SELECT 1 FROM chunks c WHERE c.doc_id=d.id"
            "                  AND c.section='abstract') THEN '摘要'"
            "      WHEN EXISTS(SELECT 1 FROM chunks c WHERE c.doc_id=d.id) THEN '仅标题'"
            "      ELSE '未清洗' END AS level,"
            " COALESCE((SELECT GROUP_CONCAT(t.tag,'、') FROM document_tags t"
            "           WHERE t.doc_id=d.id),'') AS tags_text,"
            " LENGTH(COALESCE((SELECT text FROM documents_fulltext f"
            "                  WHERE f.doc_id=d.id),'')) AS ntext"
            " FROM documents d").fetchall()]
    except Exception as exc:  #noqa: BLE001
        st.error("读取文献列表失败：%s"%exc)
        return
    for row in rows:
        row["tags"]=[
            tag for tag in str(row.pop("tags_text") or "").split("、")
            if tag
        ]

    if not rows:
        st.info("库里还没有文献。可以让 Agent 联网扩充语料，或执行 `python cli.py fetch`。")
        st.caption("如果刚才还有文献，那多半是读错了数据库：当前用的是上面那一行显示的库文件。")
        return

    ready=[r for r in rows if r["nchunk"]>0]
    pending=[r for r in rows if r["nchunk"]==0]
    col1,col2,col3=st.columns(3)
    col1.metric("库里共","%d 篇"%len(rows))
    col2.metric("可检索","%d 篇"%len(ready))
    col3.metric("未清洗","%d 篇"%len(pending))
    with st.expander("学科标签管理"):
        st.caption("标签根据题名、关键词、摘要和出版物自动生成，可同时属于多个学科。")
        if st.button("按当前规则重建全部标签",key="docs_rebuild_tags"):
            count=store.rebuild_document_tags()
            st.success("已重建 %d 条学科标签。"%count)
            st.rerun()

    all_tags=sorted({
        tag for item in rows for tag in item["tags"]
    })
    col1,col2,col3,col4=st.columns([3,2,1,2])
    with col1:
        keyword=st.text_input("标题里包含（留空 = 全部）",key="docs_kw")
    with col2:
        tag_filter=st.multiselect(
            "学科标签",all_tags,key="docs_tags",
            placeholder="全部学科",
        )
    with col3:
        only_pending=st.checkbox("只看未清洗",value=False,key="docs_pending")
    with col4:
        order=st.selectbox("排序",["标题","年份（新→旧）","片段数（多→少）",
                              "正文字数（多→少）"],key="docs_order")

    items=list(rows)
    if keyword.strip():
        needle=keyword.strip().lower()
        items=[x for x in items if needle in str(x["title"]).lower()]
    if tag_filter:
        selected=set(tag_filter)
        items=[x for x in items if selected.intersection(x["tags"])]
    if only_pending:
        items=[x for x in items if x["nchunk"]==0]
    if order.startswith("年份"):
        items.sort(key=lambda x:(x["year"] or 0),reverse=True)
    elif order.startswith("片段数"):
        items.sort(key=lambda x:x["nchunk"],reverse=True)
    elif order.startswith("正文字数"):
        items.sort(key=lambda x:x["ntext"],reverse=True)
    else:
        items.sort(key=lambda x:str(x["title"]))

    #先判空再画表格：筛选条件把结果全过滤掉时，提示要出现在表格原本的位置，
    #不能挂在表格下面，否则一眼看过去就是"表格里一篇都没有"，根本注意不到提示。
    if not items:
        reasons=[]
        if keyword.strip():
            reasons.append("标题关键词「%s」"%keyword.strip())
        if only_pending:
            reasons.append("勾选了「只看未清洗」")
        if tag_filter:
            reasons.append("学科标签为「%s」"%("、".join(tag_filter)))
        if reasons:
            st.warning("库里一共有 %d 篇文献，但当前筛选条件（%s）把它们全过滤掉了。"
                       "把关键词清空、取消勾选「只看未清洗」，就能看到全部文献。"
                       %(len(rows),"；".join(reasons)))
        else:
            st.warning("没有可显示的文献。")
        return

    st.dataframe(
        [{"#":i,"标题":x["title"],
          "年份":(str(x["year"]) if x["year"] else ""),  #整数与空串混排会让表格走慢路径
          "出版物":x["venue"] or "","来源":x["source"],
          "学科标签":"、".join(x["tags"]) or "综合类",
          "内容级别":x["level"],
          "正文字数":x["ntext"],"片段数":x["nchunk"],
          "可检索":"✅" if x["nchunk"]>0 else "—"}
         for i,x in enumerate(items,1)],
        hide_index=True,height=360,**stretch_kwargs(),
    )

    st.divider()
    st.subheader("查看 / 删除某一篇")
    labels=["%d. %s（%s）"%(i,str(x["title"])[:58],x["year"] or "n.d.")
            for i,x in enumerate(items,1)]
    picked=st.selectbox("选一篇",labels,key="docs_pick")
    one=items[labels.index(picked)]
    doc=store.get_document(one["id"],with_fulltext=False)

    if doc is not None:
        left,right=st.columns(2)
        with left:
            st.write("**学科标签**：%s"%("、".join(doc.discipline_tags) or "综合类"))
            st.write("**作者**：%s"%("、".join(a.display for a in doc.authors) or "—"))
            st.write("**年份 / 出版物**：%s ｜ %s"%(doc.year or "—",doc.venue or "—"))
            st.write("**来源**：%s"%doc.source)
        with right:
            st.write("**DOI**：%s"%(doc.doi or "—"))
            st.write("**内部 ID**：%s"%doc.id)
            try:
                from litagent.cite.gbt7714 import format_gbt7714

                st.write("**GB/T 7714**：%s"%format_gbt7714(doc.to_citation_dict()))
            except Exception:  #noqa: BLE001
                pass

        summary=store.get_summary(one["id"])
        if summary is not None:
            status_label=_summary_status_label(
                summary,doc,content_chars=one["ntext"]
            )
            if summary.is_empty():
                st.caption("结构化总结状态：尚未产出可用字段")
            else:
                st.caption("结构化总结状态：%s"%status_label)
            if status_label.startswith(("仅含概述","摘要级总结","输入过短")):
                st.caption("该文献可用正文很短，缺失字段通常无法由原文补出；不建议反复重生成。")

    with st.expander("🗑 删除这篇文献"):
        st.warning("删除会同时清掉它的正文、片段、倒排、指纹与总结，**无法撤销**。")
        confirm=st.checkbox("我确认删除《%s》"%str(one["title"])[:36],key="docs_del_ok")
        drop_pdf=st.checkbox("同时删除 data/pdfs 下已下载的 PDF",value=False,
                             key="docs_del_pdf")
        if st.button("确认删除",type="primary",disabled=not confirm,key="docs_del_btn"):
            removed=_remove_pdf(store,one["id"]) if drop_pdf else 0
            store.delete_document(one["id"])
            st.success("已删除《%s》%s。"%(str(one["title"])[:36],
                                          "，并移除 %d 个 PDF 文件"%removed if removed else ""))
            refresh_caches()
            st.rerun()

    if pending:
        st.divider()
        st.subheader("批量删除未清洗的文献")
        st.caption("这些文献只有题录、没有正文片段，检索与溯源都看不到；"
                   "删掉它们不会影响已有结果。")
        st.dataframe([{"标题":x["title"],"年份":x["year"] or "","来源":x["source"]}
                      for x in pending],
        hide_index=True,**stretch_kwargs())
        col1,col2=st.columns([2,1])
        with col1:
            bulk_ok=st.checkbox("我确认删除以上 %d 篇"%len(pending),key="docs_bulk_ok")
        with col2:
            if st.button("全部删除",disabled=not bulk_ok,key="docs_bulk_btn"):
                for x in pending:
                    store.delete_document(x["id"])
                st.success("已删除 %d 篇未清洗文献。"%len(pending))
                refresh_caches()
                st.rerun()


def tab_history()->None:
    st.header("🕘 查询历史")
    st.caption("保存 Agent 对话和溯源查询，可回看结果、打开候选原文或重新载入溯源。")
    store=get_store()
    kind_label={"trace":"溯源","agent":"Agent 对话"}
    selected_kind=st.selectbox(
        "记录类型",["全部","溯源","Agent 对话"],key="history_kind"
    )
    kind={"溯源":"trace","Agent 对话":"agent"}.get(selected_kind)
    rows=store.list_query_history(limit=300,kind=kind)
    if not rows:
        st.info("还没有查询历史。执行一次溯源或 Agent 对话后会显示在这里。")
        return

    col1,col2,col3=st.columns(3)
    col1.metric("记录数",len(rows))
    col2.metric("溯源",sum(1 for row in rows if row["kind"]=="trace"))
    col3.metric("Agent",sum(1 for row in rows if row["kind"]=="agent"))

    st.dataframe(
        [{
            "时间":row["created_at"].replace("T"," ")[:19],
            "类型":kind_label.get(row["kind"],row["kind"]),
            "查询":row["query"][:120].replace("\n"," "),
            "级别":row["level"],
            "结果数":row["result_count"],
        } for row in rows],
        hide_index=True,height=300,**stretch_kwargs(),
    )

    labels=[
        "#%d · %s · %s · %s"%(
            row["id"],kind_label.get(row["kind"],row["kind"]),
            row["created_at"].replace("T"," ")[:19],
            row["query"][:52].replace("\n"," "),
        )
        for row in rows
    ]
    picked=st.selectbox("查看一条历史",labels,key="history_pick")
    record=rows[labels.index(picked)]
    st.text_area("查询原文",record["query"],height=130,disabled=True,
                 key="history_query_view")
    metadata=record.get("metadata") or{}
    if record["kind"]=="agent" and metadata.get("answer"):
        st.markdown("**当时的回答（截断）**")
        st.write(metadata["answer"])
    candidates=metadata.get("candidates") or[]
    if candidates:
        st.markdown("**候选与链接**")
        for index,item in enumerate(candidates,start=1):
            title=str(item.get("title") or "未命名候选")
            link=_candidate_link(item)
            relation="本地" if item.get("relation")=="local" else "联网"
            if link:
                st.markdown("%d. [%s](%s)（%s）"%(index,title,link,relation))
            else:
                st.write("%d. %s（%s，无可用链接）"%(index,title,relation))

    col1,col2=st.columns([1,1])
    with col1:
        if st.button("载入溯源页",key="history_load_trace",**stretch_kwargs()):
            st.session_state.pending_trace_load={
                "query":record["query"],
                "source_hint":metadata.get("source_hint",""),
                "allow_online":bool(metadata.get("allow_online")),
            }
            st.rerun()
    with col2:
        if st.button("删除这条记录",key="history_delete",**stretch_kwargs()):
            store.delete_query_history(record["id"])
            st.rerun()

    with st.expander("清空查询历史"):
        confirm=st.checkbox("我确认清空当前筛选类型的历史",key="history_clear_ok")
        if st.button("确认清空",disabled=not confirm,key="history_clear_btn"):
            store.clear_query_history(kind=kind)
            st.rerun()

def main()->None:
    render_sidebar()

    if not get_store().exists:
        st.error("尚未建库。请先在项目目录执行：`python cli.py init`")
        return

    tabs=st.tabs(["💬 与 Agent 对话","🔍 溯源","📚 文献库","🕘 查询历史"])
    with tabs[0]:
        tab_chat()
    with tabs[1]:
        tab_trace()
    with tabs[2]:
        tab_docs()
    with tabs[3]:
        tab_history()

if __name__=="__main__":
    main()
