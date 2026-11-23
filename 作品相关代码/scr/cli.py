"""lit-agent 命令行入口。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT=Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0,str(PROJECT_ROOT))

from litagent.config import ensure_deps_on_path, get_settings

settings=get_settings()
ensure_deps_on_path(settings.deps_dir)
from litagent.logging_setup import setup_logging

setup_logging(settings,component="cli")

#Windows 控制台默认 GBK
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

#输出助手
_STATUS_LABEL={"ok":"[ OK ]","warn":"[WARN]","miss":"[MISS]","info":"[INFO]"}

def _rule(char:str="-",width:int=78)->str:
    return char*width

def print_header(title:str)->None:
    print()
    print(_rule("="))
    print("  "+title)
    print(_rule("="))

def print_rows(rows:List[Any])->None:
    """打印 [(名称, 值, 状态)] 表格。中文宽度按 2 计，保证对齐。"""
    if not rows:
        return
    def width(text:str)->int:
        return sum(2 if ord(ch)>0x2E80 else 1 for ch in str(text))

    name_w=max(width(r[0]) for r in rows)
    for row in rows:
        name,value=str(row[0]),str(row[1])
        status=str(row[2]) if len(row)>2 else "info"
        print("  %s %s%s  %s"%(_STATUS_LABEL.get(status,"[INFO]"),name,
                                " "*(name_w-width(name)),value))

def print_json(data:Any)->None:
    print(json.dumps(data,ensure_ascii=False,indent=2))

def _open_store():
    """打开库；不存在时给出提示并返回 None。"""
    from litagent.index.store import Store

    store=Store(settings.db_path)
    if not store.exists:
        print("尚未建库，请先运行 `cli.py init`。")
        return None
    store.init_schema()
    return store

#doctor
def cmd_doctor(args:argparse.Namespace)->int:
    """环境自检。这是排查问题的第一站，任何异常都先跑它。"""
    print_header("lit-agent 环境自检")

    print("\n【配置】")
    print_rows(settings.describe())

    print("\n【数据目录】")
    if args.fix:
        settings.ensure_dirs()
        print("  已创建缺失目录。")
    print_rows([(p.name,str(p),"ok" if p.is_dir() else "miss")
                for p in settings.all_dirs])
    if not all(p.is_dir() for p in settings.all_dirs):
        print("\n  提示：运行 `cli.py init` 或 `cli.py doctor --fix` 创建目录。")

    missing=settings.missing_required_deps()
    print("\n【必需依赖】")
    if missing:
        print("  缺少：%s"%", ".join(missing))
        print("  修复：python -m pip install --no-deps --target .deps "
              "-r requirements.txt")
    else:
        print("  必需依赖齐备。")

    print("\n【SQLite 索引】")
    from litagent.index.store import Store

    store=Store(settings.db_path)
    if store.exists:
        try:
            store.init_schema()
            if args.fix:
                repaired=store.repair_fulltext_flags()
                if repaired:
                    print("  已修正 %d 篇文献的「含全文」标记"%repaired)
            stats=store.stats()
            print("  文献 %d 篇（含全文 %d 篇），片段 %d 个，词条 %d 个，"
                  "指纹 %d 条，总结 %d 篇"
                  %(stats["documents"],stats["documents_with_fulltext"],
                     stats["chunks"],stats["vocabulary"],
                     stats["fingerprints"],stats["summaries"]))
        except Exception as exc:  # noqa: BLE001
            print("  [WARN] 读取索引失败：%s"%exc)
    else:
        print("  尚未建库：%s"%settings.db_path)

    print("\n【OpenCLI 浏览器补充源】")
    from litagent.fetch.opencli import OpenCLISource

    opencli_status=OpenCLISource(settings).probe()
    print_rows([("OpenCLI",opencli_status.detail,
                 "ok" if opencli_status.available else "warn")])

    if args.offline:
        print("\n【数据源连通性】已跳过（--offline）")
    else:
        print("\n【数据源连通性】")
        from litagent.fetch.base import check_sources

        print_rows(check_sources(settings))

    probe_failed=_probe_services(args)

    print("\n【结论】")
    if missing:
        print("  [MISS] 环境不完整，请先安装缺失依赖。")
        return 1
    if not settings.has_llm:
        print("  [WARN] 未配置 LLM Key —— 抓取/清洗/索引/溯源 全部可用，"
              "但「总结」与「Agent 对话」不可用。")
        print("         配置方法见 .env.example（复制成 .env 并填 LIT_LLM_API_KEY）。")
    elif probe_failed:
        print("  [FAIL] LLM Key 已配置但**调用失败**，详见上方【大模型连通性】。")
    elif not args.probe:
        print("  [ OK ] LLM 已配置（未验证可用性；加 --probe 做一次真实调用）。")
    else:
        print("  [ OK ] LLM 已配置且调用正常。")

    if not settings.contact_email:
        print("  [WARN] 未配置 LIT_CONTACT_EMAIL —— OpenAlex/Crossref 会用较低的"
              "限流配额，抓取变慢且易被限速。")
    if(not missing and settings.has_llm and not probe_failed
            and(args.probe or not settings.has_llm)):
        print("  [ OK ] 环境就绪，可以执行完整流水线。")
    return 1 if probe_failed else 0

def _probe_services(args:argparse.Namespace)->bool:
    """doctor --probe 时真正调用一次大模型，返回是否有失败。"""
    if not args.probe:
        if settings.has_llm:
            print("\n【大模型连通性】未检查（加 `--probe` 做一次真实调用）")
        return False

    print("\n【大模型连通性】")
    failed=False

    if settings.has_llm:
        from litagent.llm.client import LLMClient

        client=None
        try:
            client=LLMClient(settings)
            result=client.probe()
            if result.get("ok"):
                print("  [ OK ] %s 调用正常（%.1f 秒，模型 %s）"
                      %(settings.llm_model,result.get("elapsed_seconds",0),
                         result.get("model","")))
            else:
                failed=True
                print("  [FAIL] %s 调用失败：%s"
                      %(settings.llm_model,result.get("error","未知原因")))
                _print_probe_hint(result.get("error",""))
        except Exception as exc:  # noqa: BLE001
            failed=True
            print("  [FAIL] 初始化客户端失败：%s: %s"
                  %(type(exc).__name__,str(exc)[:200]))
        finally:
            if client is not None:
                client.close()
    else:
        print("  [MISS] 未配置 LLM Key，跳过")

    return failed

def _print_probe_hint(error:str)->None:
    """针对探测失败给出**可操作**的排查建议，而不是只丢一个状态码。"""
    text=(error or "").lower()
    hints=[]
    if "401" in text or "403" in text or "invalid" in text or "auth" in text:
        hints.append("Key 可能写错或已失效 —— 到服务商后台重新复制一次")
    if "404" in text:
        hints.append("端点或模型名不对 —— 检查 LIT_LLM_BASE_URL 是否漏了 /v1，"
                     "以及 LIT_LLM_MODEL 是否是该服务商支持的模型")
    if "402" in text or "quota" in text or "balance" in text or "insufficient" in text:
        hints.append("余额或额度不足 —— 到服务商后台确认")
    if "429" in text:
        hints.append("被限流 —— 稍后重试，或降低 LIT_LLM_MAX_CONCURRENCY")
    if "timeout" in text or "timed out" in text or "connect" in text:
        hints.append("网络不通 —— 若需要代理，设置 LIT_PROXY")
    for hint in hints:
        print("         建议：%s"%hint)
    if not hints:
        print("         建议：把 .env 里的 LIT_LLM_BASE_URL / LIT_LLM_MODEL 与"
              "服务商文档核对一遍")

#init / stats
def cmd_init(args:argparse.Namespace)->int:
    """创建数据目录与 SQLite schema（幂等，可重复执行）。"""
    print_header("初始化 lit-agent")
    settings.ensure_dirs()
    print("  数据目录已就绪：%s"%settings.data_dir)

    from litagent.index.store import SCHEMA_VERSION, Store
    from litagent.schema import utcnow

    store=Store(settings.db_path)
    store.init_schema()
    tables=store.table_names()
    print("  SQLite 已建库：%s"%settings.db_path)
    print("  表（%d）：%s"%(len(tables),", ".join(tables)))

    store.set_meta("schema_version",SCHEMA_VERSION)
    store.set_meta("created_at",utcnow())
    print("\n  下一步：")
    print("    cli.py fetch --source openalex --query \"your topic\" --limit 20")
    return 0

def cmd_stats(args:argparse.Namespace)->int:
    """打印全库统计。"""
    store=_open_store()
    if store is None:
        return 1
    stats=store.stats()

    if args.json:
        print_json(stats)
        return 0

    print_header("语料统计")
    print("  数据库        : %s (%.1f MB)"
          %(stats["db_path"],stats["db_size_bytes"]/1024.0/1024.0))
    print("  文献          : %d 篇（其中含全文 %d 篇）"
          %(stats["documents"],stats["documents_with_fulltext"]))
    print("  片段          : %d 个，共 %d 词"%(stats["chunks"],stats["tokens_total"]))
    print("  倒排索引      : %d 条 posting，%d 个不同词条"
          %(stats["postings"],stats["vocabulary"]))
    print("  精确指纹      : %d 条"%stats["fingerprints"])
    print("  结构化总结    : %d 篇"%stats["summaries"])
    print("  LLM 缓存      : %d 条"%stats["llm_cache"])

    for key,label in(("by_source","来源"),("by_venue_type","文献类型"),
                       ("by_language","语言"),("by_year","年份(近 20)")):
        data=stats.get(key) or{}
        if data:
            print("  %-13s : %s"%(label,", ".join(
                "%s=%s"%(k,v) for k,v in list(data.items())[:12])))
    return 0

#fetch / download
def cmd_fetch(args:argparse.Namespace)->int:
    """从在线数据源或本地目录抓取文献。"""
    from litagent.fetch.runner import FetchRunner

    store=_open_store()
    if store is None:
        return 1
    runner=FetchRunner(store,settings)

    def progress(state:str,query:str,count:int)->None:
        label={"done":"完成","failed":"失败","skip":"跳过"}.get(state,state)
        if state=="skip":
            print("  [%s] %s（之前已抓取）"%(label,query))
        else:
            print("  [%s] %s -> %d 条"%(label,query,count))

    print_header("阶段 1：文章获取")

    if args.local:
        paths=list(args.path or[])
        if not paths:
            print("  请用 --path 指定本地文献目录或文件。")
            return 1
        print("  扫描本地路径：%s"%", ".join(paths))

        def local_progress(done:int,total:int,path:Any)->None:
            if done==total or done%25==0:
                print("    [%d/%d] %s"%(done,total,str(path)[-70:]))

        report=runner.fetch_local(paths,recursive=not args.no_recursive,
                                    on_progress=local_progress)
        print()
        print(report.summary())
        return 0

    if not args.query:
        print("  请用 --query 指定检索词（可重复多次），或用 --local --path 扫描本地。")
        return 1

    print("  数据源：%s"%args.source)
    print("  检索词：%s"%" | ".join(args.query))
    print()

    report=runner.fetch_online(
        args.source,args.query,
        limit_per_query=args.limit,
        year_from=args.year_from,
        year_to=args.year_to,
        venue_type=args.venue_type,
        open_access_only=args.oa_only,
        resume=args.resume,
        archive=not args.no_archive,
        on_progress=progress,
    )

    if args.pdf:
        print("\n  下载开放获取 PDF（最多 %d 篇）..."%args.pdf)
        pdf_report=runner.download_pdfs(
            limit=args.pdf,source_name=args.source,
            on_progress=lambda done,total,title:print(
                "    [%d/%d] %s"%(done,total,title)),
        )
        report.pdfs_downloaded=pdf_report.pdfs_downloaded
        report.pdf_failures=pdf_report.pdf_failures
        report.errors.extend(pdf_report.errors)

    print()
    print(report.summary())

    stats=store.stats()
    print("\n  当前语料：%d 篇文献（含全文 %d 篇）"
          %(stats["documents"],stats["documents_with_fulltext"]))
    return 0

def cmd_download(args:argparse.Namespace)->int:
    """下载开放获取全文 PDF。"""
    from litagent.fetch.runner import FetchRunner

    store=_open_store()
    if store is None:
        return 1

    runner=FetchRunner(store,settings)
    print_header("下载开放获取全文")
    print("  最多尝试 %d 篇，时间预算 %.0f 秒"%(args.limit,args.budget or 900))

    report=runner.download_pdfs(
        doc_ids=args.id or None,
        limit=args.limit,
        budget_seconds=args.budget,
        on_progress=lambda done,total,title:print(
            "    [%d/%d] %s"%(done,total,title)),
    )
    print()
    print("  成功 %d 篇，失败 %d 篇，耗时 %.1f 秒"
          %(report.pdfs_downloaded,report.pdf_failures,report.elapsed_seconds))
    if report.errors:
        print("\n  失败原因（这是正常的：多数期刊论文不是开放获取）：")
        for error in report.errors[:10]:
            print("    - "+error[:150])
    if report.pdfs_downloaded:
        print("\n  下一步：cli.py clean --redo  然后  cli.py index --rebuild")
    return 0

#clean / index
def cmd_clean(args:argparse.Namespace)->int:
    """清洗与分块。"""
    from litagent.clean.pipeline import CleanPipeline

    store=_open_store()
    if store is None:
        return 1

    pipeline=CleanPipeline(store,settings)
    print_header("阶段 2：数据清洗")

    repaired=store.repair_fulltext_flags()
    if repaired:
        print("  已修正 %d 篇文献的「含全文」标记"%repaired)
        print()

    def progress(index:int,total:int,doc:Any)->None:
        if args.verbose or index==total or index%20==0:
            print("  [%d/%d] %s"%(index,total,doc.title[:70]))

    report=pipeline.run(
        doc_ids=args.id or None,
        limit=args.limit,
        redo=args.redo,
        with_fulltext_only=args.fulltext_only,
        run_dedupe=args.dedupe,
        on_progress=progress,
    )
    print()
    print(report.summary())

    stats=store.stats()
    print("\n  当前语料：%d 篇文献，%d 个片段，词条 %d 个"
          %(stats["documents"],stats["chunks"],stats["vocabulary"]))

    if args.sample and not args.quiet:
        print("\n  ---- 片段抽样 ----")
        shown=0
        for chunk in store.iter_chunks():
            if shown>=args.sample:
                break
            doc=store.get_document(chunk.doc_id,with_fulltext=False)
            print("  [%s] 章节=%s 页=%s 词数=%d"
                  %(doc.short_ref() if doc else chunk.doc_id,
                     chunk.section,chunk.page_start,chunk.token_count))
            print("     "+chunk.text[:200].replace("\n"," ")+" ...")
            shown+=1
    return 0

def cmd_index(args:argparse.Namespace)->int:
    """构建 BM25 / 精确指纹两路索引。"""
    from litagent.index.builder import IndexBuilder

    store=_open_store()
    if store is None:
        return 1

    builder=IndexBuilder(store,settings)
    print_header("阶段 3：构建索引")

    state={"stage":""}

    def progress(stage:str,index:int,total:int)->None:
        if stage!=state["stage"]:
            state["stage"]=stage
            print("  [%s]"%("BM25 + 精确指纹" if stage=="lexical" else stage))
        if index==total or index%25==0:
            print("    [%d/%d]"%(index,total))

    report=builder.build_all(
        doc_ids=args.id or None,
        rebuild=args.rebuild,
        on_progress=progress,
    )
    print()
    print(report.summary())

    stats=store.stats()
    print("\n  语料现状：%d 篇文献，%d 个片段，%d 条 posting，"
          "%d 个不同词条，%d 条指纹"
          %(stats["documents"],stats["chunks"],stats["postings"],
             stats["vocabulary"],stats["fingerprints"]))
    return 0

#search
def cmd_search(args:argparse.Namespace)->int:
    """在本地语料中检索文献或正文片段。"""
    store=_open_store()
    if store is None:
        return 1

    query=" ".join(args.query).strip()
    if not query:
        print("请提供检索词，例如：cli.py search \"图神经网络 推荐系统\"")
        return 1

    from litagent.retrieve.search import HybridSearcher

    searcher=HybridSearcher(store,settings)
    if args.documents:
        items,report=searcher.search_documents(query,top_k=args.top_k)
        rows=[
            {
                "title":doc.title,
                "authors":[author.display for author in doc.authors],
                "year":doc.year,
                "venue":doc.venue,
                "doi":doc.doi,
                "url":doc.url,
                "score":round(score,6),
            }
            for doc,score,_ in items
        ]
    else:
        hits,report=searcher.search(query,top_k=args.top_k)
        rows=[
            {
                "title":hit.doc.title if hit.doc else "",
                "year":hit.doc.year if hit.doc else None,
                "venue":hit.doc.venue if hit.doc else None,
                "doi":hit.doc.doi if hit.doc else None,
                "url":hit.doc.url if hit.doc else None,
                "chunk_id":hit.chunk_id,
                "section":hit.section,
                "page":hit.page,
                "score":round(hit.score,6),
                "snippet":hit.text[:260],
            }
            for hit in hits
        ]

    if args.json:
        print_json({"report":report.summary(),"results":rows})
        return 0

    print_header("本地检索")
    print("  "+report.summary())
    if not rows:
        print("  没有找到匹配结果。")
        return 0
    for index,row in enumerate(rows,start=1):
        print()
        print("  %d. %s"%(index,row.get("title") or row.get("chunk_id")))
        meta=" | ".join(str(value) for value in(
            row.get("year"),row.get("venue"),row.get("doi")
        ) if value)
        if meta:
            print("     "+meta)
        if row.get("snippet"):
            print("     "+row["snippet"].replace("\n"," "))
    return 0

#summarize
def cmd_summarize(args:argparse.Namespace)->int:
    """LLM 结构化总结。"""
    from litagent.summarize.runner import SummaryRunner

    store=_open_store()
    if store is None:
        return 1

    print_header("深度学习 / 结构化总结")

    if args.export:
        runner=SummaryRunner(store,settings)
        try:
            print("  已导出总结到：%s"%runner.export_summaries())
        finally:
            runner.close()
        return 0

    if not settings.has_llm:
        print("  [WARN] 未配置大模型 API Key：只读取已有缓存，未命中缓存的文献会失败。")
        print("  配置方法：在 %s 中写入"%(settings.project_root/".env"))
        print("      LIT_LLM_API_KEY=sk-你的DeepSeek密钥")
        print()

    from litagent.summarize.prompts import PROMPT_VERSION

    runner=SummaryRunner(store,settings)
    print("  模型：%s（并发 %d）"
          %(settings.llm_model,args.concurrency or settings.llm_max_concurrency))
    print("  提示词版本：%s"%PROMPT_VERSION)
    print()

    def progress(index:int,total:int,title:str)->None:
        print("    [%d/%d] %s"%(index,total,title))

    try:
        report=runner.run(
            doc_ids=args.id or None,
            limit=args.limit,
            redo=args.redo,
            with_claims=not args.no_claims,
            concurrency=args.concurrency,
            on_progress=progress,
        )
    finally:
        runner.close()

    print()
    print(report.summary())

    if args.export_jsonl:
        exporter=SummaryRunner(store,settings)
        try:
            print("\n  已导出：%s"%exporter.export_summaries())
        finally:
            exporter.close()

    if args.sample and report.processed:
        print("\n  ---- 抽样 ----")
        for row in store.connect().execute(
            "SELECT * FROM summaries ORDER BY created_at DESC LIMIT ?",(args.sample,)
        ):
            summary=store._row_to_summary(row)
            doc=store.get_document(summary.doc_id,with_fulltext=False)
            print("\n  《%s》"%(doc.title[:70] if doc else summary.doc_id))
            print("  一句话：%s"%summary.tldr)
            if summary.methods:
                print("  方法  ：%s"%summary.methods[:160])
            if summary.findings:
                print("  发现  ：%s"%summary.findings[:160])
            for claim in summary.claims[:2]:
                print("  可引用：%s"%claim.text[:160])
    return 0

#trace
def cmd_trace(args:argparse.Namespace)->int:
    """溯源：找出这段话出自哪篇论文，并给出 GB/T 7714 引用。"""
    from litagent.retrieve.trace import QuoteTracer

    quote=" ".join(args.quote)
    if args.file:
        path=Path(args.file)
        if not path.is_file():
            print("文件不存在：%s"%path)
            return 1
        quote=path.read_text(encoding="utf-8",errors="replace").strip()
    if not quote:
        print("请通过位置参数或 --file 提供要溯源的文本。")
        return 1

    store=_open_store()
    if store is None:
        return 1

    tracer=QuoteTracer(store,settings)
    print_header("溯源")
    print("  查询文本（前 200 字）：")
    print("    "+quote[:200].replace("\n"," "))
    print()

    result,report=tracer.trace(
        quote,allow_online=not args.no_online,top_k=args.top_k,
        online_interactive=True,source_hint=args.source_hint,
    )
    print(tracer.explain(result))
    print()
    print("  "+report.summary())
    if report.config_warning:
        print("  [WARN] "+report.config_warning)
    if report.online_error:
        print("  联网错误：%s"%report.online_error)

    if args.json:
        print()
        print_json(result.to_dict())
    return 0

#chat
def cmd_chat(args:argparse.Namespace)->int:
    """与文献 Agent 对话。"""
    from litagent.agent.loop import LiteratureAgent

    store=_open_store()
    if store is None:
        return 1

    if not settings.has_llm:
        print_header("与文献 Agent 对话")
        print("  [MISS] 未配置大模型 API Key，Agent 无法工作。")
        print()
        print("  配置方法：在 %s 中写入"%(settings.project_root/".env"))
        print("      LIT_LLM_API_KEY=sk-你的DeepSeek密钥")
        print("  申请地址：https://platform.deepseek.com/")
        print()
        print("  提示：不依赖大模型的「溯源」仍然可用 —— ")
        print("      cli.py trace \"一段话\"")
        return 2

    agent=LiteratureAgent(store,settings)
    question=" ".join(args.question) if args.question else ""

    if not question and not args.interactive:
        print("请提供问题，例如：")
        print("  cli.py chat \"帮我总结一下库里那篇讲古籍缀合的论文\"")
        print("  cli.py chat \"这段话出自哪里：<粘贴一段话>\"")
        print("  或加 --interactive 进入多轮对话")
        agent.close()
        return 1

    print_header("lit-agent 文献助手")
    print("  语料：%d 篇文献（含全文 %d 篇）"
          %(store.count_documents(),store.count_documents(only_with_fulltext=True)))
    print("  模型：%s"%settings.llm_model)
    print()

    def show_step(step:Any)->None:
        if args.verbose:
            print("  ┌ thought: %s"%step.thought[:200])
            if step.tool:
                print("  │ action : %s(%s)"
                      %(step.tool,json.dumps(step.arguments,ensure_ascii=False)[:160]))
            if step.error:
                print("  │ error  : %s"%step.error[:160])
            if step.observation:
                print("  └ result : %s"%step.observation[:300])
            print()
        elif step.tool:
            print("  · 调用 %s ..."%step.tool,flush=True)

    try:
        if args.interactive:
            history:List[Dict[str,str]]=[]
            while True:
                try:
                    line=input("\n你 > ").strip()
                except(EOFError,KeyboardInterrupt):
                    print()
                    break
                if not line:
                    continue
                if line.lower() in("exit","quit","退出",":q"):
                    break
                result=agent.run(line,history=history,on_step=show_step)
                print("\nAgent > %s"%result.answer)
                print("\n  （%d 轮，%.1f 秒，输入 %d / 输出 %d token）"
                      %(result.rounds,result.elapsed_seconds,
                         result.prompt_tokens,result.completion_tokens))
                history.append({"role":"user","content":line})
                history.append({"role":"assistant","content":result.answer})
                #只保留最近 6 轮，避免上下文无限膨胀
                history=history[-12:]
            return 0

        result=agent.run(question,on_step=show_step)
        print()
        print(result.answer)
        print()
        print("  ── %d 轮，%.1f 秒，输入 %d / 输出 %d token，停止原因：%s"
              %(result.rounds,result.elapsed_seconds,result.prompt_tokens,
                 result.completion_tokens,result.stopped_reason))
        if args.trace:
            print()
            print(result.transcript())
        if args.json:
            print()
            print_json(result.to_dict())
        return 0
    finally:
        agent.close()

#delete
def _remove_pdf_file(store:Any,doc_id:str)->int:
    """删除该文献已下载的 PDF。只动项目目录以内的文件，越界一律跳过。"""
    doc=store.get_document(doc_id,with_fulltext=False)
    raw=getattr(doc,"pdf_path",None) if doc is not None else None
    if not raw:
        return 0
    try:
        target=Path(raw).resolve()
        if settings.project_root.resolve() not in target.parents:  #防止库里的路径指向项目外
            return 0
        if target.is_file():
            target.unlink()
            return 1
    except Exception:
        return 0
    return 0

def cmd_delete(args:argparse.Namespace)->int:
    """删除文献及其派生数据。"""
    store=_open_store()
    if store is None:
        return 1

    rows=[dict(r) for r in store.connect().execute(
        "SELECT d.id, d.title, d.year, d.source,"
        " (SELECT COUNT(*) FROM chunks c WHERE c.doc_id=d.id) AS nchunk"
        " FROM documents d ORDER BY d.title").fetchall()]
    if not rows:
        print("库里还没有文献。")
        return 0

    targets:List[Dict[str,Any]]=[]
    if args.id:
        by_id={r["id"]:r for r in rows}
        for doc_id in args.id:
            hit=by_id.get(doc_id.strip())
            if hit is None:
                print("  [MISS] 找不到文献：%s"%doc_id)
            elif hit not in targets:
                targets.append(hit)
    if args.uncleaned:
        for r in rows:
            if r["nchunk"]==0 and r not in targets:
                targets.append(r)

    if not targets:
        print("没有指定要删除的文献。用法：")
        print("  cli.py delete --id doi:10.1234/abcd      # 按 ID 删除，可重复")
        print("  cli.py delete --uncleaned                # 删除所有未清洗（没有片段）的文献")
        print("  加 --with-pdf 同时删除 data/pdfs 下已下载的 PDF，加 --yes 跳过确认。")
        return 1

    print_header("删除文献")
    for r in targets:
        print("  - [%d 个片段] %s"%(r["nchunk"],str(r["title"])[:70]))
    print()
    print("  共 %d 篇。删除会同时清掉正文、片段、倒排、指纹与总结，**无法撤销**。"
          %len(targets))

    if not args.yes:
        try:
            answer=input("  确认删除？输入 yes 继续：").strip().lower()
        except(EOFError,KeyboardInterrupt):
            answer=""
        if answer not in("yes","y","是"):
            print("  已取消，什么都没删。")
            return 0

    removed_pdf=0
    for r in targets:
        if args.with_pdf:
            removed_pdf+=_remove_pdf_file(store,r["id"])
        store.delete_document(r["id"])

    print("  已删除 %d 篇文献%s。"
          %(len(targets),"，并移除 %d 个 PDF 文件"%removed_pdf if removed_pdf else ""))
    stats=store.stats()
    print("  当前语料：%d 篇文献（含全文 %d 篇），%d 个片段"
          %(stats["documents"],stats["documents_with_fulltext"],stats["chunks"]))
    return 0

def cmd_ui(args:argparse.Namespace)->int:
    """启动 Streamlit 网页界面。"""
    app_path=settings.project_root/"app.py"
    if not app_path.is_file():
        print("找不到界面文件：%s"%app_path)
        return 1

    port=args.port or settings.streamlit_port
    print_header("启动 lit-agent 网页界面")
    print("  地址：http://127.0.0.1:%d"%port)
    print("  按 Ctrl+C 停止。")
    print()

    try:
        from streamlit.web import cli as stcli_main
    except ImportError:
        print("  [MISS] 未安装 streamlit。")
        return 1

    sys.argv=["streamlit","run",str(app_path),
                "--server.port",str(port),
                "--server.headless","true",
                "--browser.gatherUsageStats","false"]
    return int(stcli_main.main() or 0)

#参数解析
def build_parser()->argparse.ArgumentParser:
    parser=argparse.ArgumentParser(
        prog="cli.py",
        description="lit-agent —— 面向论文写作的文献检索 Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("示例:\n"
                "  cli.py doctor --probe\n"
                "  cli.py stats\n"
                "  cli.py chat \"帮我找几篇文本校勘的论文\"\n"
                "  cli.py trace \"一段要溯源的话\"\n"
                "  cli.py ui\n"),
    )
    sub=parser.add_subparsers(dest="command",metavar="<命令>")

    p=sub.add_parser("doctor",help="环境自检")
    p.add_argument("--fix",action="store_true",help="自动创建缺失的数据目录")
    p.add_argument("--offline",action="store_true",help="跳过联网连通性检查")
    p.add_argument("--probe",action="store_true",
                   help="真正调用一次大模型，验证 Key 是否可用"
                        "（会产生极少量调用费用）")
    p.set_defaults(func=cmd_doctor)

    p=sub.add_parser("init",help="建库（目录 + SQLite schema）")
    p.set_defaults(func=cmd_init)

    p=sub.add_parser("stats",help="打印全库统计")
    p.add_argument("--json",action="store_true",help="以 JSON 输出")
    p.set_defaults(func=cmd_stats)

    p=sub.add_parser("search",help="检索本地文献或正文片段")
    p.add_argument("query",nargs="+",help="检索词")
    p.add_argument("--top-k",type=int,default=10,help="返回条数")
    p.add_argument("--documents",action="store_true",
                   help="按文献聚合，而不是返回正文片段")
    p.add_argument("--json",action="store_true",help="以 JSON 输出")
    p.set_defaults(func=cmd_search)

    p=sub.add_parser("fetch",help="抓取文献")
    p.add_argument("--source",default="openalex",
                   choices=["openalex","crossref","arxiv","semanticscholar","opencli"],
                   help="在线数据源（默认 openalex）")
    p.add_argument("--query","-q",action="append",default=[],
                   help="检索词，可重复多次")
    p.add_argument("--limit",type=int,default=25,help="每个检索词抓取条数")
    p.add_argument("--year-from",type=int,default=None,help="起始年份")
    p.add_argument("--year-to",type=int,default=None,help="结束年份")
    p.add_argument("--venue-type",default=None,
                   choices=["journal","conference","book","chapter","thesis",
                            "report","standard","patent","newspaper","web",
                            "dataset","preprint"],
                   help="只看某类文献")
    p.add_argument("--oa-only",action="store_true",help="只要开放获取")
    p.add_argument("--resume",action="store_true",help="跳过之前已完成的检索词")
    p.add_argument("--no-archive",action="store_true",help="不写原始 JSONL 归档")
    p.add_argument("--local",action="store_true",help="扫描本地文献目录")
    p.add_argument("--path",action="append",default=[],
                   help="本地目录或文件，可重复（配合 --local）")
    p.add_argument("--no-recursive",action="store_true",help="本地扫描不递归子目录")
    p.add_argument("--pdf",type=int,default=0,metavar="N",
                   help="额外下载最多 N 篇开放获取 PDF")
    p.set_defaults(func=cmd_fetch)

    p=sub.add_parser("download",help="下载开放获取全文 PDF")
    p.add_argument("--id",action="append",default=[],help="只下载指定文献 ID")
    p.add_argument("--limit",type=int,default=20,help="最多下载多少篇")
    p.add_argument("--budget",type=float,default=900.0,
                   help="总时间预算（秒），防止个别死链拖住整批")
    p.set_defaults(func=cmd_download)

    p=sub.add_parser("clean",help="清洗数据")
    p.add_argument("--id",action="append",default=[],help="只清洗指定文献 ID")
    p.add_argument("--limit",type=int,default=None,help="最多清洗多少篇")
    p.add_argument("--redo",action="store_true",help="强制重做（默认增量，跳过已清洗的）")
    p.add_argument("--fulltext-only",action="store_true",
                   help="只处理有 PDF/全文的，跳过仅有摘要的")
    p.add_argument("--dedupe",action="store_true",help="清洗后对全库跑一次去重合并")
    p.add_argument("--sample",type=int,default=0,metavar="N",
                   help="清洗后抽查 N 个片段")
    p.add_argument("--verbose",action="store_true",help="打印每篇的处理进度")
    p.add_argument("--quiet",action="store_true",help="不打印抽样")
    p.set_defaults(func=cmd_clean)

    p=sub.add_parser("index",help="构建索引")
    p.add_argument("--id",action="append",default=[],help="只索引指定文献 ID")
    p.add_argument("--rebuild",action="store_true",help="全量重建（默认增量）")
    p.set_defaults(func=cmd_index)

    p=sub.add_parser("summarize",help="LLM 结构化总结")
    p.add_argument("--id",action="append",default=[],help="只总结指定文献 ID")
    p.add_argument("--limit",type=int,default=None,help="最多总结多少篇")
    p.add_argument("--redo",action="store_true",
                   help="重做已有总结（默认断点续跑，跳过已完成的）")
    p.add_argument("--no-claims",action="store_true",help="跳过论断抽取（省一半调用）")
    p.add_argument("--concurrency",type=int,default=None,help="并发数")
    p.add_argument("--sample",type=int,default=0,metavar="N",help="完成后抽查 N 篇")
    p.add_argument("--export",action="store_true",help="只导出已有总结，不调用模型")
    p.add_argument("--export-jsonl",action="store_true",help="完成后导出 JSONL")
    p.set_defaults(func=cmd_summarize)

    p=sub.add_parser("delete",help="删除文献及其索引数据")
    p.add_argument("--id",action="append",default=[],help="要删除的文献 ID，可重复")
    p.add_argument("--uncleaned",action="store_true",
                   help="删除所有未清洗（没有正文片段）的文献")
    p.add_argument("--with-pdf",action="store_true",
                   help="同时删除 data/pdfs 下已下载的 PDF 文件")
    p.add_argument("--yes","-y",action="store_true",help="跳过确认，直接删除")
    p.set_defaults(func=cmd_delete)

    p=sub.add_parser("trace",help="溯源：这段话出自哪篇论文")
    p.add_argument("quote",nargs="*",help="要溯源的文本（可直接粘贴）")
    p.add_argument("--file",help="从文件读取要溯源的文本")
    p.add_argument("--no-online",action="store_true",help="本地未命中时不联网")
    p.add_argument("--source-hint",default="",
                   help="CNKI 链接、DOI 或其他来源提示，用于优先解析题录")
    p.add_argument("--top-k",type=int,default=5,help="返回候选数")
    p.add_argument("--json",action="store_true",help="额外输出 JSON")
    p.set_defaults(func=cmd_trace)

    p=sub.add_parser("chat",help="与 Agent 对话")
    p.add_argument("question",nargs="*",help="你的问题")
    p.add_argument("--interactive","-i",action="store_true",help="进入多轮对话")
    p.add_argument("--verbose","-v",action="store_true",help="显示 Agent 的思考过程")
    p.add_argument("--trace",action="store_true",help="结束后打印完整执行轨迹")
    p.add_argument("--json",action="store_true",help="额外输出 JSON")
    p.set_defaults(func=cmd_chat)

    p=sub.add_parser("ui",help="启动网页界面")
    p.add_argument("--port",type=int,default=None,help="端口（默认 8501）")
    p.set_defaults(func=cmd_ui)

    return parser

def main(argv:Optional[List[str]]=None)->int:
    parser=build_parser()
    args=parser.parse_args(argv)
    if not getattr(args,"command",None):
        parser.print_help()
        return 0
    return int(args.func(args) or 0)

if __name__=="__main__":
    raise SystemExit(main())
