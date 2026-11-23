"""在线数据源共用的 HTTP 基础设施。

HttpClient 负责限流、重试、磁盘缓存和 PDF 下载。Source 定义各数据源
共同遵守的查询接口。
"""
from __future__ import annotations

import hashlib
import importlib
import json
import random
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

from litagent.config import Settings
from litagent.config import settings as default_settings
from litagent.schema import Document

try:  # 允许在没装 requests 的环境下仍能 import 本模块（便于静态检查）
    import requests
    from requests import Response
except ImportError:  # pragma: no cover
    requests=None  # type: ignore[assignment]
    Response=Any  # type: ignore[misc,assignment]


# 令牌桶限流
class TokenBucket:
    """
    线程安全令牌桶
    'rate'为每秒补充的令牌数（即QPS上限），'capacity'为桶容量（默认等于 1 秒的量，允许小幅突发）。
    """

    def __init__(self,rate:float,capacity:Optional[float]=None)->None: #构造函数：接受速率'rate'与可选容量'capacity'，容量为None时自动推算
        self.rate=max(rate,0.001) #保存速率并保证不小于0.001
        self.capacity=float(capacity if capacity is not None else max(self.rate,1.0)) #容量默认取速率与1.0中的较大者，至少留1个令牌的突发空间
        self._tokens=self.capacity
        self._last=time.monotonic() #用单调时钟几下当前时刻，算间隔
        self._lock=threading.Lock()  #创建线程锁，保证多线程同时取令牌时不会算错

    def acquire(self,tokens:float=1.0,timeout:Optional[float]=None)->bool:  #默认取1个，可传超时秒数
        """取走'tokens'个令牌；不够则阻塞等待,超时返回 False"""
        deadline=None if timeout is None else time.monotonic()+timeout  #算出截止时刻，若timeout为None就一直等下去
        while True:  #死循环直到取到令牌或者超时才退出
            with self._lock:  #同一时刻只允许一个线程修改令牌数
                now=time.monotonic()  #读当前单调时间
                elapsed=now-self._last  #算距离上次的秒数
                self._last=now  #更新时间
                self._tokens=min(self.capacity,self._tokens+elapsed*self.rate)
                if self._tokens>=tokens:  #令牌够用往下扣
                    self._tokens-=tokens
                    return True
                deficit=tokens-self._tokens  #算出还差多少令牌
                wait=deficit/self.rate  #差额/速率=还要等几秒才有足够令牌
            if deadline is not None and time.monotonic()+wait>deadline:  #若算上此次等待时间会超时，放弃并返回False
                return False
            time.sleep(min(wait,5.0))  #小睡一会重试，封顶5秒

# HTTP 客户端
class HttpError(RuntimeError):
    """HTTP 请求最终失败（重试耗尽）"""
    def __init__(self,message:str,*,status:Optional[int]=None,
                 url:str="",retryable:bool=False)->None:
        """接受错误消息并用仅关键字参数接受状态码与地址"""
        super().__init__(message)  #把消息交给'RuntimeError'打印异常
        self.status=status  #状态码存成属性，区分404or500
        self.url=url   #把出错的URL存成属性，方便日志定位问题接口
        self.retryable=bool(retryable)


def _looks_like_pdf(block:bytes)->bool:
    """判断一段字节流开头是不是 PDF。

    规范要求 ``%PDF`` 出现在文件前 1024 字节内（允许前面有少量垃圾字节）。
    """
    if not block:
        return False
    return b"%PDF" in block[:1024]


class HttpClient:
    """带限流、重试、磁盘缓存的 HTTP 客户端。

    典型用法::

        client = HttpClient(rate=8.0, user_agent="lit-agent/0.1 (mailto:a@b.c)")
        data = client.get_json("https://api.openalex.org/works", params={"search": "gnn"})
    """

    #: 这些状态码值得重试
    RETRY_STATUS=frozenset({408,425,429,500,502,503,504,522,524})

    def __init__(
        self,
        rate:float=1.0,
        *,
        timeout:float=30.0,
        max_retries:int=4,
        backoff_base:float=1.5,
        user_agent:str="lit-agent/0.1",
        proxy:str="",
        cache_dir:Optional[Path]=None,
        use_cache:bool=True,
        referer:str="",
    )->None:
        if requests is None:  # pragma: no cover
            raise RuntimeError("未安装 requests，无法发起网络请求")
        self.bucket=TokenBucket(rate)
        self.timeout=timeout
        self.max_retries=max(0,max_retries)
        self.backoff_base=max(1.05,backoff_base)
        self.user_agent=user_agent
        self.proxy=proxy or ""
        self.cache_dir=cache_dir
        self.use_cache=use_cache and cache_dir is not None
        self.referer=referer
        self._session=requests.Session()
        self._session.headers.update({
            "User-Agent":user_agent,
            "Accept-Encoding":"gzip, deflate",
        })
        if referer:
            self._session.headers["Referer"]=referer

    #缓存
    def _cache_path(self,url:str,params:Optional[Dict[str,Any]])->Optional[Path]:  #根据URL与参数算出缓存文件的路径
        if not self.use_cache or self.cache_dir is None:  #没开缓存或没给目录就返回None，据此跳过缓存逻辑
            return None
        key=url+"?"+urlencode(sorted((params or{}).items()),doseq=True)  #把参数按键排序后编码成查询串再与URL拼成缓存键，排序保证参数顺序不同也能命中同一份缓存
        digest=hashlib.sha1(key.encode("utf-8")).hexdigest()  #用'sha1'把缓存键散列成十六进制摘要，避免文件名里出现非法字符
        return self.cache_dir/(digest+".json")  #缓存目录下的摘要+.json

    def _cache_read(self,url:str,params:Optional[Dict[str,Any]])->Optional[Any]:
        path=self._cache_path(url,params)
        if path is None or not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except(OSError,ValueError):
            return None

    def _cache_write(self,url:str,params:Optional[Dict[str,Any]],
                     payload:Any)->None:
        path=self._cache_path(url,params)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(json.dumps(payload,ensure_ascii=False),
                            encoding="utf-8")
        except OSError:
            pass

    # -- 请求 --------------------------------------------------------------
    def _proxies(self)->Optional[Dict[str,str]]:
        if not self.proxy:
            return None
        return{"http":self.proxy,"https":self.proxy}

    def request(self,method:str,url:str,*,params:Optional[Dict[str,Any]]=None,
                headers:Optional[Dict[str,str]]=None,data:Any=None,
                json_body:Any=None,allow_404:bool=False,
                stream:bool=False)->Optional["Response"]:  # type: ignore[reportInvalidTypeForm]
        """发起请求，自动限流与重试。返回 ``None`` 表示 404 且 ``allow_404=True``。"""
        last_error:Optional[Exception]=None
        last_retryable:bool=False

        for attempt in range(self.max_retries+1):
            self.bucket.acquire(1.0)
            try:
                response=self._session.request(
                    method,
                    url,
                    params=params,
                    headers=headers,
                    data=data,
                    json=json_body,
                    timeout=self.timeout,
                    proxies=self._proxies(),
                    stream=stream,
                )
            except Exception as exc:  # noqa: BLE001
                last_error=exc
                last_retryable=True
                if attempt>=self.max_retries:
                    break
                self._backoff(attempt,None)
                continue

            if response.status_code==404 and allow_404:
                response.close()
                return None

            if response.status_code in self.RETRY_STATUS:
                retry_after=self._parse_retry_after(response.headers.get("Retry-After"))
                last_error=HttpError(
                    "HTTP %d"%response.status_code,
                    status=response.status_code,url=url,retryable=True,
                )
                last_retryable=True
                if attempt>=self.max_retries:
                    break
                response.close()
                self._backoff(attempt,retry_after)
                continue

            if response.status_code>=400:
                raise HttpError(
                    "HTTP %d: %s"%(response.status_code,response.text[:300]),
                    status=response.status_code,url=url,retryable=False,
                )
            return response

        raise HttpError(
            "请求失败（已重试 %d 次）：%s"%(self.max_retries,last_error),
            status=getattr(last_error,"status",None),url=url,
            retryable=last_retryable or bool(
                getattr(last_error,"retryable",False)
            ),
        )

    @staticmethod
    def _parse_retry_after(value:Optional[str])->Optional[float]:
        if not value:
            return None
        try:
            return max(0.0,float(value))
        except(TypeError,ValueError):
            return None

    def _backoff(self,attempt:int,retry_after:Optional[float])->None:
        """指数退避 + 抖动。尊重服务端给出的 Retry-After。"""
        if retry_after is not None:
            delay=retry_after
        else:
            delay=(self.backoff_base**attempt)+random.uniform(0.0,0.75)
        time.sleep(min(delay,60.0))

    # -- 便捷方法 ----------------------------------------------------------
    def get_json(self,url:str,*,params:Optional[Dict[str,Any]]=None,
                 headers:Optional[Dict[str,str]]=None,
                 allow_404:bool=False,use_cache:Optional[bool]=None)->Optional[Any]:
        """GET 并解析 JSON。命中磁盘缓存则直接返回，不发请求。"""
        cache_on=self.use_cache if use_cache is None else(use_cache and self.use_cache)
        if cache_on:
            cached=self._cache_read(url,params)
            if cached is not None:
                return cached

        response=self.request("GET",url,params=params,headers=headers,
                                allow_404=allow_404)
        if response is None:
            return None
        try:
            payload=response.json()
        finally:
            response.close()
        if cache_on:
            self._cache_write(url,params,payload)
        return payload

    def get_text(self,url:str,*,params:Optional[Dict[str,Any]]=None,
                 headers:Optional[Dict[str,str]]=None,
                 allow_404:bool=False)->Optional[str]:
        """GET 并返回文本（arXiv 的 Atom XML 用这个）。"""
        response=self.request("GET",url,params=params,headers=headers,
                                allow_404=allow_404)
        if response is None:
            return None
        try:
            return response.text
        finally:
            response.close()
#流式下载与原子落盘

    def download(self,url:str,dest:Path,*,headers:Optional[Dict[str,str]]=None,  #'headers'设为只传关键字，避免调用方误把第三个位置参数当成请求头
                 chunk_size:int=1<<16,max_bytes:Optional[int]=80*1024*1024,  #分块大小取'1<<16'即64KB：太小会让大文件多跑上千次循环，太大又占内存
                 require_pdf:bool=False)->Path:
        """流式下载文件到'dest'，返回最终落盘的路径

            边收边写不进内存，因此超大PDF也不会撑爆进程。参数说明分两块：
            一是'require_pdf'，开启后校验响应体开头的PDF魔数，强烈建议对论文下载开启——
            文献元数据里的'oa_url'经常指向出版社的落地页HTML而不是真正的PDF直链，
            不校验就会把一坨HTML存成'.pdf'，后续解析全部失败且很难排查；
            二是'max_bytes'，体积上限，防止误下载超大文件把磁盘塞满。
            已存在且非空的文件会直接跳过。
        """
        dest=Path(dest)
        if dest.is_file() and dest.stat().st_size>1024:  #只在文件非空且超过1KB时才复用，0字节的残file当初就是写坏的，必须重下
            if not require_pdf or self._is_pdf_file(dest):  #不要求PDF时直接放行；要求PDF时还得过一遍魔数，旧版可能存的是落地页HTML
                return dest
            #重下之前先把旧的错文件删掉，否则下面的'.part'改名会撞上这个占位
            try:
                dest.unlink()  #删不掉就算了，让后面的写入去报错，不在这里把异常抛给上层
            except OSError:
                pass

        dest.parent.mkdir(parents=True,exist_ok=True)  #'parents=True'连同多级父目录一起建，'exist_ok=True'让已存在时不报错
        response=self.request("GET",url,headers=headers,stream=True)  #走统一入口发请求，从而白拿限流、退避重试和代理三样东西
        if response is None:
            raise HttpError("下载失败（404）：%s"%url,status=404,url=url)  #这里异常里硬编404：其实是重试耗尽，但上层只把404当成'取不到正文'

        tmp=dest.with_suffix(dest.suffix+".part")  #先写同目录下的临时名，同目录才能保证'rename'是原子操作，跨盘就会变成复制
        written=0
        try:
            with open(tmp,"wb") as handle:  #'wb'二进制写，PDF是字节流不能走文本模式，否则会被换行符转换弄坏
                first_block=True  #只有第一块才需要验魔数，后面几千块再验既费时又没意义
                for block in response.iter_content(chunk_size=chunk_size):  #边下边写不进内存，否则一篇几百MB的超大PDF能把进程撑爆
                    if not block:  #空块直接跳过：某些服务端会隔着发空片，写进去只会白占体积
                        continue
                    if first_block:  #验完这一块就置False，保证魔数校验只发生一次
                        first_block=False
                        if require_pdf and not _looks_like_pdf(block):  #第一块就不是PDF，说明拿回来的是HTML落地页，早退比下完再发现省得多
                            content_type=response.headers.get("Content-Type","?")  #取不到'Content-Type'就给'?'占位，只为把响应头写进报错里便于排查
                            raise HttpError(
                                "响应不是 PDF（Content-Type=%s）：%s"%(content_type,url),  #报错里带上'Content-Type'和服务地址，是这类问题定位的唯一线索
                                url=url,
                            )
                    written+=len(block)
                    if max_bytes and written>max_bytes:  #'max_bytes'为0或None时视为不限，方便调试时手工放开
                        raise HttpError("文件超过 %d 字节上限：%s"%(max_bytes,url),url=url)
                    handle.write(block)  #校验全部通过之后才落盘，避免把半截内容写到临时文件里占空间

            if written<512:  #过小的文件基本是错误页或空壳，与其留着让下游解析器报莫名的错不如这里拦下
                raise HttpError("文件过小（%d 字节），可能不是有效文档：%s"
                                %(written,url),url=url)
            if require_pdf and not self._is_pdf_file(tmp):  #再对落盘结果整体复验一次：磁盘上的头1024字节才是最终事实
                raise HttpError("下载内容缺少 PDF 文件头：%s"%url,url=url)
            tmp.replace(dest)  #改名是原子替换，要么看到完整旧文件要么看到完整新文件，不会出现半成品
        except BaseException:  #用'BaseException'兜住键盘中断和系统退出，保证临时文件在任何路径下都被清掉
            if tmp.exists():  #先判断存在再删，并发场景下别的进程可能已经抢先删过了
                try:
                    tmp.unlink()  #临时文件删不掉也不能顶替原始异常，这里静默吞掉保住真正的报错
                except OSError:
                    pass
            raise
        finally:
            response.close()  #无论成功失败都关连接，'stream'模式下不关会把连接池占死
        return dest

    #PDF魔数校验
    @staticmethod
    def _is_pdf_file(path:Path)->bool:
        """检查文件头是否为PDF魔数，只看开头一小段而不读整个文件"""
        try:
            with open(path,"rb") as handle:  #只读开头1024字节而不是整个文件，几百MB的PDF不会因此被读进内存
                return _looks_like_pdf(handle.read(1024))
        except OSError:  #文件不存在或没权限时返回False，调用方拿到的是'不是PDF'而不是异常
            return False


#数据源基类
# ---------------------------------------------------------------------------
# 数据源基类
# ---------------------------------------------------------------------------
class Source(ABC):
    """在线文献数据源抽象基类

        子类需要实现'search'，并按能力实现'by_doi'、'by_id'、'pdf_url'。
        所有方法都不允许抛异常打断整条流水线——失败时返回空列表或'None'，
        由'FetchRunner'汇总上报，这样一个源挂掉不会拖垮整轮抓取。
    """

    #: 数据源短名，用于 Document.source 与命令行 --source
    name:str="base"  #类属性写成默认值，子类只覆盖自己关心的那几项，不用重写整个构造函数
    #: 该源默认 QPS
    default_rate:float=1.0  #各源官方要求的QPS不一样，arXiv要3秒一次，所以限流值必须由子类自己声明
    #: 是否支持关键词检索
    supports_search:bool=True
    #: 是否支持按 DOI 精确取
    supports_doi:bool=True
    base_url:str=""

    def __init__(self,settings:Optional[Settings]=None,*,  #只传'on settings'的位置参数，其余关键字，防止调用方把缓存开关误当第二个位置参数
                 use_cache:bool=True)->None:
        self.settings=settings or default_settings  #没传就落到全局默认配置，让各子类不用各自处理'None'分支
        self.client=self._make_client(use_cache=use_cache)  #客户端在构造时一次建好，'self.client'持着会话，避免每个请求都重新握手

    #构造
    # -- 构造 --------------------------------------------------------------
    def user_agent(self)->str:
        """带联系邮箱的UA——OpenAlex和Crossref的polite pool靠它识别"""
        contact=self.settings.contact_email
        ua="lit-agent/0.1 (https://github.com/local/lit-agent)"  #UA里带上邮箱才进polite pool，配额和稳定性都明显好过匿名请求
        if contact:
            ua+=" (mailto:%s)"%contact  #'mailto:'是OpenAlex和Crossref约定的写法，少了这个前缀它们认不出来
        return ua

    def _make_client(self,*,use_cache:bool=True)->HttpClient:
        cache_dir=None  #先设成None，只有真开了缓存才赋值，否则会把'None/...'这种坏路径拼出来
        if use_cache:
            cache_dir=self.settings.cache_dir/"http"/self.name  #按数据源名分子目录，各源的缓存互不干扰，清了arXiv也不会牵连Crossref
        return HttpClient(
            rate=self.rate(),
            timeout=self.settings.http_timeout,
            max_retries=self.settings.http_max_retries,
            backoff_base=self.settings.http_backoff_base,
            user_agent=self.user_agent(),
            proxy=self.settings.requests_proxy,
            cache_dir=cache_dir,
            use_cache=use_cache,
        )

    def rate(self)->float:  #抽成方法而不是直接读类属性，子类就能按当日配额或时段动态算限流值
        """返回该源的QPS上限，默认取类属性，子类可覆盖以实现动态限流"""
        return self.default_rate  #默认返回类属性，保持与旧代码的兼容，不影响自定义子类

    def polite_params(self)->Dict[str,Any]:
        """各源通用的礼貌参数，子类可覆盖"""
        params:Dict[str,Any]={}  #用类型标注先声明成字典，后面再逐项塞，避免子类拿到'None'再去判断
        if self.settings.contact_email:
            params["mailto"]=self.settings.contact_email  #没有联系邮箱就不带这个参数，硬塞空串反而会被服务端当成无效标识
        return params

    #能力接口
    # -- 能力接口 ----------------------------------------------------------
    @abstractmethod
    def search(self,query:str,*,limit:int=20,offset:int=0,  #'search'用'abstractmethod'强制子类实现，漏写的话构造阶段就会报错而不是跑到一半才炸
               **filters:Any)->List[Document]:
        """关键词检索，返回Document列表"""

    def by_doi(self,doi:str)->Optional[Document]:
        """按DOI精确取一篇，默认不支持"""
        return None  #默认返回'None'表示不支持，调用方按能力探测即可，不必逐个'hasattr'

    def by_id(self,source_id:str)->Optional[Document]:
        """按数据源内部ID取一篇，默认不支持"""
        return None
  #同上，按数据源内部ID取是可选能力，不支持就返回'None'
    def pdf_url(self,doc:Document)->Optional[str]:
        """返回可下载的OA全文PDF地址，没有则'None'"""
        return None

    def close(self)->None:
        try:
            self.client._session.close()  # noqa: SLF001
        except Exception:  # noqa: BLE001
            pass


#注册表
# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------
_SOURCE_REGISTRY:Dict[str,type]={}


def register_source(cls:type)->type:
    """把数据源类登记进注册表（作为装饰器使用）"""
    name=getattr(cls,"name","")  #用'getattr'取名字、取不到给空串，这样没有'name'的类也不会直接崩
    if name:  #没有名字就不登记，让它在注册表里隐身，而不是用一个空键把别人覆盖掉
        _SOURCE_REGISTRY[name]=cls
    return cls


def available_sources()->List[str]:
    """返回已注册的数据源名称"""
    _load_builtin_sources()
    return sorted(_SOURCE_REGISTRY)


def get_source(name:str,settings:Optional[Settings]=None,*,
               use_cache:bool=True)->Source:
    """按名称构造数据源实例"""
    _load_builtin_sources()
    key=(name or "").strip().lower()
    if key not in _SOURCE_REGISTRY:  #查不到就抛'KeyError'并列出可用的源，省得调用方对着'None'猜自己拼错了什么
        raise KeyError(
            "未知数据源 %r，可用：%s"%(name,", ".join(sorted(_SOURCE_REGISTRY)))  #'%r'给名字加引号，空串和空格这类看不见的错误也能一眼看出来
        )
    return _SOURCE_REGISTRY[key](settings,use_cache=use_cache)  #把'use_cache'透传下去，命令行加'--no-cache'时才能真的绕过磁盘缓存


def _load_builtin_sources()->None:
    """延迟导入内置数据源，避免循环导入与无谓的网络库加载"""
    for name in ("arxiv","crossref","local_pdf","openalex","opencli","s2"):
        importlib.import_module("litagent.fetch."+name)


#连通性检查
# ---------------------------------------------------------------------------
# 连通性检查（供 cli.py doctor 使用）
# ---------------------------------------------------------------------------
def check_sources(settings:Optional[Settings]=None,
                  timeout:float=8.0)->List[Tuple[str,str,str]]:
    """逐个探测数据源，返回[(名称, 说明, 状态)]三元组列表

        返回三元组而不是布尔值，是为了让doctor能逐行打印每个源的具体状态。
        这里刻意用短超时加单次尝试：探测只回答通不通，超时压到8秒且不重试，
        不能让自检自己把命令行卡住。
    """
    settings=settings or default_settings  #统一兜到全局默认配置，调用方不传也能跑
    rows:List[Tuple[str,str,str]]=[]

    if requests is None:  #没装requests就早退，后面几行都要用它，继续下去只会连报四个同样的错
        return[("requests","未安装，无法联网","miss")]  # type: ignore[list-item]，状态标'miss'而不是'warn'：缺依赖是硬伤，和'被限流'这种软问题要区分开

    probes=[
        ("openalex","https://api.openalex.org/works",
         {"search":"test","per-page":1}),
        ("crossref","https://api.crossref.org/works",
         {"query":"test","rows":1}),
        ("arxiv","https://export.arxiv.org/api/query",
         {"search_query":"all:test","max_results":1}),
        ("semanticscholar","https://api.semanticscholar.org/graph/v1/paper/search",
         {"query":"test","limit":1}),
    ]

    session=requests.Session()
    session.headers.update({"User-Agent":"lit-agent/0.1 doctor"})  #doctor用自己的UA，不夹邮箱，免得自检的流量被算进正式配额里
    proxies=None  #先设成None再按配置填，没配代理时'requests'就按环境变量走，不要硬塞空字典
    if settings.requests_proxy:
        proxies={"http":settings.requests_proxy,"https":settings.requests_proxy}  #'http'和'https'分别给值，只给一个的话另一路请求会静默直连

    for name,url,params in probes:
        try:
            response=session.get(url,params=params,timeout=timeout,
                                   proxies=proxies,allow_redirects=True)  #允许跟随跳转，很多接口的裸域名会先302到真正的工作地址
            code=response.status_code  #先取出状态码再分支，避免下面四次都重复访问属性
            if code==200:
                rows.append((name,"HTTP 200","ok"))
            elif code==429:
                rows.append((name,"HTTP 429 被限流（可正常使用，会自动退避重试）","warn"))  #429要专门提示'可正常使用'，否则用户看到被限流就以为源挂了
            elif code==403:
                rows.append((name,"HTTP 403 需要鉴权/密钥","warn"))  #403单独列出来，提醒是缺密钥而不是网络不通，两件事的排查方向完全不同
            else:
                rows.append((name,"HTTP %d"%code,"warn"))  #其余状态码只报数字不猜原因，免得把404说成限流误导排查
            response.close()  #每条探测完立刻关响应，几十次连发不关会攒下一堆未释放的连接
        except Exception as exc:  # noqa: BLE001
            rows.append((name,"%s: %s"%(type(exc).__name__,  #异常只截前60个字符并把换行压成空格，保证自检输出是一行一条便于阅读
                                           str(exc)[:60].replace("\n"," ")),"miss"))

    session.close()
    rows.append(("contact_email",
                 settings.contact_email or "未配置（限流配额较低）",
                 "ok" if settings.contact_email else "warn"))  #配了就标'ok'，没配给'warn'而不是'miss'——不配也能跑，只是配额低
    return rows


__all__=[
    "HttpClient",
    "HttpError",
    "TokenBucket",
    "Source",
    "register_source",
    "get_source",
    "available_sources",
    "check_sources",
]
