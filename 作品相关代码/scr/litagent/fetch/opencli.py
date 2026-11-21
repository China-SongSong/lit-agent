"""通过 OpenCLI 子进程补充浏览器型学术来源。

OpenCLI 只作为二级在线来源：返回题录候选，不下载全文、不写本地库，
也不参与本地引文溯源判定。命令缺失、浏览器断连或站点失败都只影响
本通道，不阻断 OpenAlex/Crossref/arXiv/Semantic Scholar。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor,as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any,Dict,List,Optional,Sequence,Tuple

from litagent.config import Settings,settings as default_settings
from litagent.fetch.base import Source,register_source
from litagent.index.lexical import tokenize
from litagent.schema import Document,has_cjk,normalize_doi,parse_authors

_WS_RE=re.compile(r"\s+")
_YEAR_RE=re.compile(r"\b((?:19|20)\d{2})\b")
_CHINESE_ACADEMIC_HINTS=("学位","论文","学报","期刊","会议","研究","文献","知识图谱")
_NPX_FALLBACK=("npx","-y","@jackwener/opencli@2.0.3")
_QUERY_SEPARATOR_RE=re.compile(r"[\s,，、；;。！？!?：:\"'“”‘’（）()\[\]【】]+")


class OpenCLIError(RuntimeError):
    """OpenCLI 命令执行或响应解析失败。"""


@dataclass(frozen=True)
class OpenCLIAvailability:
    """OpenCLI 运行时可探测状态。"""

    available:bool
    command:str
    detail:str


def _balanced_json(text:str)->Optional[str]:
    start=next((index for index,char in enumerate(text) if char in "{["),-1)
    if start<0:
        return None
    opening=text[start]
    closing="}" if opening=="{" else "]"
    depth=0
    in_string=False
    escaped=False
    for index in range(start,len(text)):
        char=text[index]
        if in_string:
            if escaped:
                escaped=False
            elif char=="\\":
                escaped=True
            elif char=='"':
                in_string=False
            continue
        if char=='"':
            in_string=True
        elif char==opening:
            depth+=1
        elif char==closing:
            depth-=1
            if depth==0:
                return text[start:index+1]
    return None


def parse_cli_payload(stdout:str)->Any:
    """解析 OpenCLI 标准 JSON 输出，容忍少量日志前后缀。"""
    text=(stdout or "").strip()
    if not text:
        raise OpenCLIError("OpenCLI 没有返回 stdout")
    try:
        payload=json.loads(text)
    except ValueError:
        balanced=_balanced_json(text)
        if not balanced:
            raise OpenCLIError("OpenCLI stdout 不是 JSON：%s"%text[:200])
        try:
            payload=json.loads(balanced)
        except ValueError as exc:
            raise OpenCLIError("OpenCLI JSON 解析失败：%s"%balanced[:200]) from exc

    if isinstance(payload,dict) and payload.get("ok") is False:
        error=payload.get("error") or{}
        if isinstance(error,dict):
            message=error.get("message") or error.get("code") or str(error)
        else:
            message=str(error)
        raise OpenCLIError(message)
    if isinstance(payload,dict) and "data" in payload:
        payload=payload["data"]
    if isinstance(payload,dict):
        for key in("items","rows","results"):
            if isinstance(payload.get(key),list):
                return payload[key]
    return payload


def _split_authors(value:Any)->List[str]:
    if value is None:
        return[]
    if isinstance(value,list):
        return[
            str(item.get("name") or item.get("display_name") or item).strip()
            if isinstance(item,dict) else str(item).strip()
            for item in value
            if item
        ]
    return[
        item.strip()
        for item in re.split(r"\s*(?:[,，;；、]|\band\b)\s*",str(value))
        if item.strip()
    ]


def _year(value:Any)->Optional[int]:
    match=_YEAR_RE.search(str(value or ""))
    return int(match.group(1)) if match else None


def _venue_type(value:Any)->str:
    text=str(value or "").lower()
    if any(word in text for word in("thesis","dissertation","学位","硕士","博士")):
        return "thesis"
    if any(word in text for word in("conference","proceedings","会议")):
        return "conference"
    if any(word in text for word in("patent","专利")):
        return "patent"
    if any(word in text for word in("book","图书","专著")):
        return "book"
    if any(word in text for word in("newspaper","报纸")):
        return "newspaper"
    return "journal"


def chinese_metadata_query(query:str,*,limit:int=6)->str:
    """把中文正文压成适合知网/万方的题录关键词。"""
    tokens=tokenize(query or "")
    ordered:List[str]=[]
    for token in tokens:
        token=token.strip()
        if token and has_cjk(token) and token not in ordered:
            ordered.append(token)
    # 搜索模式会同时给出长词和子词，优先保留信息量更大的完整词。
    if any(len(token)>=2 for token in ordered):
        ordered=[token for token in ordered if len(token)>=2]
    if not ordered:
        ordered=[
            part for part in _QUERY_SEPARATOR_RE.split(query or "")
            if part
        ]
    return " ".join(ordered[:max(1,int(limit))])


def exact_quote_query(
    query:str,*,minimum_chars:int=18,maximum_chars:int=48
)->str:
    """选取完整句中的一段做引号精确检索，避免发送超长正文。"""
    text=_WS_RE.sub(" ",(query or "").strip())
    if not text:
        return ""
    clauses=[
        part.strip()
        for part in re.split(r"(?<=[。！？；;.!?])|\n+",text)
        if part.strip()
    ]
    segment=""
    for clause in clauses:
        if len(clause)>=minimum_chars:
            segment=clause
            break
    segment=segment or text
    if len(segment)>maximum_chars:
        segment=segment[:maximum_chars]
    return '"%s"'%segment.replace('"','').strip()


def row_to_document(row:Dict[str,Any],site:str)->Optional[Document]:
    """把各学术适配器的公共字段映射成 Document。"""
    title=_WS_RE.sub(" "," ".join([
        str(row.get("title") or ""),
        str(row.get("subtitle") or ""),
    ])).strip()
    if not title:
        return None
    authors=parse_authors(_split_authors(row.get("authors") or row.get("author")))
    doi=normalize_doi(row.get("doi"))
    url=str(
        row.get("url") or row.get("link") or row.get("paper_url") or ""
    ).strip() or None
    source=f"opencli:{site}"
    source_id=str(row.get("id") or url or title)
    venue=(
        row.get("venue") or row.get("source") or row.get("journal")
        or row.get("container_title")
    )
    cited=row.get("cited") or row.get("citation_count") or row.get("citations")
    try:
        citation_count=int(cited) if cited not in(None,"") else None
    except(TypeError,ValueError):
        citation_count=None

    return Document(
        id=Document.make_id(source,source_id,doi,title),
        title=title,
        authors=authors,
        doi=doi,
        year=_year(row.get("year") or row.get("date") or row.get("published")),
        venue=_WS_RE.sub(" ",str(venue)).strip() if venue else None,
        venue_type=_venue_type(row.get("type") or row.get("venue_type")),
        url=url,
        language="zh" if has_cjk(title) else "en",
        source=source,
        source_id=source_id,
        citation_count=citation_count,
        extra={
            "opencli_site":site,
            "opencli_rank":row.get("rank"),
            "opencli_raw":dict(row),
            "candidate_tier":"browser_supplement",
        },
    )


@register_source
class OpenCLISource(Source):
    """OpenCLI 学术站点的只读补充适配器。"""

    name="opencli"
    default_rate=1.0
    supports_search=True
    supports_doi=False

    _circuit_lock=threading.Lock()
    _failures:Dict[str,int]={}
    _disabled_until:Dict[str,float]={}

    def __init__(self,settings:Optional[Settings]=None,*,use_cache:bool=True)->None:
        self.settings=settings or default_settings
        self.use_cache=use_cache
        self.last_errors:List[str]=[]

    def _command_parts(self)->List[str]:
        command=(self.settings.opencli_command or "").strip()
        if not command:
            return[]
        exact=Path(command)
        if exact.is_file():
            return[str(exact)]
        if Path(command.strip('"')).name.lower()=="opencli":
            bundled_candidates=(
                self.settings.project_root/"vendor"/"opencli-cli"/"node_modules"
                /"@jackwener"/"opencli"/"dist"/"src"/"main.js",
                self.settings.project_root.parent/"vendor"/"opencli-cli"/"node_modules"
                /"@jackwener"/"opencli"/"dist"/"src"/"main.js",
            )
            node=shutil.which("node")
            bundled=next((path for path in bundled_candidates if path.is_file()),None)
            if bundled is not None and node:
                return[node,str(bundled)]
        parts=shlex.split(command,posix=False)
        if(
            parts
            and Path(parts[0].strip('"')).name.lower()=="opencli"
            and not shutil.which(parts[0].strip('"'))
            and shutil.which("npx")
        ):
            parts=list(_NPX_FALLBACK)
            node=shutil.which("node")
            npx=shutil.which("npx")
            if node and npx:
                npx_cli=(
                    Path(npx).resolve().parent
                    /"node_modules"/"npm"/"bin"/"npx-cli.js"
                )
                if npx_cli.is_file():
                    return[node,str(npx_cli),*parts[1:]]
        resolved=shutil.which(parts[0].strip('"')) if parts else None
        if resolved:
            parts[0]=resolved
        return parts

    def availability(self)->OpenCLIAvailability:
        parts=self._command_parts()
        if not parts:
            return OpenCLIAvailability(False,"","未配置 LIT_OPENCLI_COMMAND")
        executable=parts[0].strip('"')
        if Path(executable).is_file():
            bundled_main=(
                len(parts)>1
                and Path(parts[1]).name.lower()=="main.js"
                and "opencli-cli" in str(Path(parts[1])).lower()
            )
            return OpenCLIAvailability(
                True,str(Path(executable)),
                "项目内置固定版本：%s"%_NPX_FALLBACK[-1]
                if bundled_main else "可执行文件存在",
            )
        resolved=shutil.which(executable)
        if resolved:
            bundled_main=(
                len(parts)>1
                and Path(parts[1]).name.lower()=="main.js"
                and "opencli-cli" in str(Path(parts[1])).lower()
            )
            detail=(
                "通过项目内置固定版本：%s"%_NPX_FALLBACK[-1]
                if bundled_main
                else "通过固定版本 npx 后备：%s"%_NPX_FALLBACK[-1]
                if resolved.lower().endswith(("npx","npx.cmd"))
                else "命令在 PATH 中"
            )
            return OpenCLIAvailability(True,resolved,detail)
        return OpenCLIAvailability(
            False,
            executable,
            "未找到 OpenCLI 命令；安装 @jackwener/opencli 或设置 LIT_OPENCLI_COMMAND",
        )

    def probe(self)->OpenCLIAvailability:
        status=self.availability()
        if not status.available:
            return status
        try:
            result=self._run(
                ["--version"],timeout=self.settings.opencli_timeout
            )
            detail=(result.stdout or result.stderr or "").strip()[:160]
            return OpenCLIAvailability(True,status.command,detail or "版本探测成功")
        except Exception as exc:
            return OpenCLIAvailability(
                False,status.command,"%s: %s"%(type(exc).__name__,str(exc)[:160])
            )

    def _cache_path(self,site:str,query:str,limit:int)->Path:
        key="%s\0%s\0%d"%(site,query,limit)
        digest=hashlib.sha1(key.encode("utf-8")).hexdigest()
        return self.settings.cache_dir/"opencli"/"v2"/site/(digest+".json")

    def _cache_read(self,site:str,query:str,limit:int)->Optional[List[Document]]:
        if not self.use_cache or self.settings.opencli_cache_ttl_seconds<=0:
            return None
        path=self._cache_path(site,query,limit)
        if not path.is_file():
            return None
        try:
            if time.time()-path.stat().st_mtime>self.settings.opencli_cache_ttl_seconds:
                return None
            payload=json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload,list):
                return None
            return[Document.from_dict(item) for item in payload if isinstance(item,dict)]
        except(OSError,ValueError,TypeError):
            return None

    def _cache_write(
        self,site:str,query:str,limit:int,documents:Sequence[Document]
    )->None:
        if not self.use_cache or self.settings.opencli_cache_ttl_seconds<=0:
            return
        path=self._cache_path(site,query,limit)
        try:
            path.parent.mkdir(parents=True,exist_ok=True)
            tmp=path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps([doc.to_dict() for doc in documents],ensure_ascii=False),
                encoding="utf-8",
            )
            tmp.replace(path)
        except OSError:
            pass

    def _run(self,args:Sequence[str],*,timeout:float)->subprocess.CompletedProcess:
        parts=self._command_parts()
        if not parts:
            raise OpenCLIError("未配置 OpenCLI 命令")
        if self.settings.opencli_profile:
            args=["--profile",self.settings.opencli_profile,*args]
        flags=(
            getattr(subprocess,"CREATE_NO_WINDOW",0)
            | getattr(subprocess,"CREATE_NEW_PROCESS_GROUP",0)
        )
        command=[*parts,*args]
        if Path(parts[0].strip('"')).suffix.lower() in(".cmd",".bat"):
            command_line=subprocess.list2cmdline(command)
            command=[
                os.environ.get("COMSPEC",r"C:\Windows\System32\cmd.exe"),
                "/d","/s","/c",command_line,
            ]
        environment=dict(os.environ)
        environment.setdefault(
            "OPENCLI_BROWSER_CONNECT_TIMEOUT",
            str(max(1.0,min(float(timeout),8.0))),
        )
        environment.setdefault(
            "OPENCLI_BROWSER_COMMAND_TIMEOUT",
            str(max(1.0,min(float(timeout),120.0))),
        )
        process=subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=flags,
            env=environment,
        )
        try:
            stdout,stderr=process.communicate(timeout=max(0.1,timeout))
        except subprocess.TimeoutExpired as exc:
            if os.name=="nt":
                subprocess.run(
                    ["taskkill","/PID",str(process.pid),"/T","/F"],
                    capture_output=True,
                    creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0),
                    check=False,
                )
            else:
                process.kill()
            try:
                process.communicate(timeout=2.0)
            except subprocess.TimeoutExpired:
                pass
            raise OpenCLIError(
                "OpenCLI 命令在 %.1f 秒内未返回（浏览器桥或站点响应超时）"
                %float(timeout)
            ) from exc
        return subprocess.CompletedProcess(
            command,process.returncode,stdout,stderr
        )

    @classmethod
    def _site_blocked(cls,site:str)->bool:
        with cls._circuit_lock:
            return time.monotonic()<cls._disabled_until.get(site,0.0)

    def _record_site_result(self,site:str,ok:bool)->None:
        with self._circuit_lock:
            if ok:
                self._failures[site]=0
                self._disabled_until.pop(site,None)
                return
            failures=self._failures.get(site,0)+1
            self._failures[site]=failures
            if failures>=self.settings.opencli_circuit_failures:
                self._disabled_until[site]=(
                    time.monotonic()+self.settings.opencli_circuit_cooldown_seconds
                )

    def _site_timeout(self,site:str,*,interactive:bool)->float:
        if not interactive:
            return float(self.settings.opencli_timeout)
        configured=float(self.settings.opencli_interactive_timeout)
        floor={
            "cnki":30.0,
            "wanfang":20.0,
            "baidu-scholar":20.0,
            "google-scholar":15.0,
        }.get(site,float(self.settings.opencli_timeout))
        return max(configured,floor)

    @staticmethod
    def query_for_site(site:str,query:str)->str:
        """中文站使用题录关键词，学术/网页检索使用精确短语。"""
        if site in("cnki","wanfang"):
            return chinese_metadata_query(query) or exact_quote_query(query)
        if site in("baidu-scholar","google-scholar"):
            return exact_quote_query(query) or query
        return query

    def _search_site(
        self,site:str,query:str,limit:int,*,interactive:bool=False
    )->List[Document]:
        cached=self._cache_read(site,query,limit)
        if cached is not None:
            return cached
        if self._site_blocked(site):
            raise OpenCLIError("%s 当前处于熔断冷却期"%site)
        search_query=self.query_for_site(site,query)
        result=self._run(
            [site,"search",search_query,"--limit",str(limit),"-f","json"],
            timeout=self._site_timeout(site,interactive=interactive),
        )
        if result.returncode!=0:
            self._record_site_result(site,False)
            try:
                parse_cli_payload(result.stdout)
            except OpenCLIError as exc:
                raise OpenCLIError("%s: %s"%(site,str(exc)[:220])) from exc
            raise OpenCLIError(
                "%s 返回退出码 %d：%s"
                %(site,result.returncode,(result.stderr or result.stdout or "")[:180])
            )
        rows=parse_cli_payload(result.stdout)
        if not isinstance(rows,list):
            self._record_site_result(site,False)
            raise OpenCLIError("%s 返回的数据不是列表"%site)
        documents=[
            doc for doc in(row_to_document(row,site) for row in rows if isinstance(row,dict))
            if doc is not None
        ][:limit]
        self._cache_write(site,query,limit,documents)
        self._record_site_result(site,True)
        return documents

    @staticmethod
    def select_sites(query:str)->Tuple[str,...]:
        text=query or ""
        if has_cjk(text):
            return("cnki","wanfang","baidu-scholar")
        return("google-scholar",)

    def search_with_errors(
        self,query:str,*,limit:int=4,interactive:bool=False
    )->Tuple[List[Document],List[str]]:
        runtime=self.availability()
        if not runtime.available:
            return[],["opencli: "+runtime.detail]
        sites=self.select_sites(query)
        per_site=max(1,min(int(limit),20))
        documents:List[Document]=[]
        errors:List[str]=[]
        with ThreadPoolExecutor(max_workers=len(sites),thread_name_prefix="opencli") as pool:
            futures={pool.submit(
                self._search_site,site,query,per_site,interactive=interactive
            ):site
                     for site in sites}
            for future in as_completed(futures):
                site=futures[future]
                try:
                    rows=future.result()
                    documents.extend(rows)
                    if not rows:
                        errors.append(
                            "%s: 0 条（若页面要求登录或安全验证，请先完成）"%site
                        )
                except Exception as exc:
                    errors.append("%s: %s"%(site,str(exc)[:180]))
        self.last_errors=errors
        return documents,errors

    def search(self,query:str,*,limit:int=4,**filters:Any)->List[Document]:
        documents,_=self.search_with_errors(query,limit=limit)
        return documents

    def close(self)->None:
        return


__all__=[
    "OpenCLIError",
    "OpenCLIAvailability",
    "OpenCLISource",
    "chinese_metadata_query",
    "exact_quote_query",
    "parse_cli_payload",
    "row_to_document",
]
