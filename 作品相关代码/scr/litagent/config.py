"""集中读取路径、网络和模型参数，生成全局 Settings。"""
from __future__ import annotations

import os
import math
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List,Optional,Tuple

PACKAGE_DIR:Path=Path(__file__).resolve().parent  #包成Path对象，并.resolve变成绝对路径
PROJECT_ROOT:Path=PACKAGE_DIR.parent

#.env 允许放在代码目录，也允许往上放若干级（代码放在 scr 子目录、.env 放在作品根目录就是这种情况）
ENV_SEARCH_PARENTS:int=2

def find_env_file()->Optional[Path]:
    """从代码目录开始逐级往上找 .env，返回第一个找到的；都没有则返回 None。"""
    base=PROJECT_ROOT
    for _ in range(ENV_SEARCH_PARENTS+1):
        candidate=base/".env"
        if candidate.is_file():
            return candidate
        parent=base.parent
        if parent==base:  #已经到盘符根，再往上没有了
            break
        base=parent
    return None

def load_dotenv(path=None,override=False)->int:  #.env解释器
    """从.env文件加载环境变量并返回成功加载的条目数"""
    path=path or (PROJECT_ROOT/".env")
    if not path.is_file():
        return 0  #定路径+早退：没传就用默认（合法的空值也当成没给）
    loaded=0
    try:
        content=path.read_text(encoding="utf-8-sig")
    except(OSError,UnicodeDecodeError):  #接住两类异常，（元组）表示两种都抓
        return 0
    for raw_line in content.splitlines():
        line=raw_line.strip()  #去掉一行首尾的空白
        if not line or line.startswith("#"):
            continue 
        if line.startswith("export "):
            line=line[len("export "):].strip()
        if "=" not in line:
            continue  #没有= ->不是配置项，跳过
        key,_,value=line.partition("=")
        key,value=key.strip(),value.strip()
        #剥掉包裹的引号
        if len(value)>=2 and value[0]==value[-1] and value[0] in "\"'":
            value=value[1:-1]
        if not key:
            continue 
        #真正写进去+计数，同时计数器+1
        if override or key not in os.environ:
            os.environ[key]=value
            loaded+=1
    return loaded

def _env_first(*names:str)->Optional[str]:
    """按顺序返回第一个非空环境变量值"""
    for name in names:
        value=os.environ.get(name)  #用os.environ.get取值
        if value is not None and value.strip()!="":
            return value.strip()
    return None

def env_str(*names:str,default:str="")->str:  #关键字传参
    value=_env_first(*names)
    return default if value is None else value


def _in_range(
    value:float,*,minimum:Optional[float]=None,maximum:Optional[float]=None
)->bool:
    """拒绝非有限值和越界值，让错误配置回退到明确默认值。"""
    if not math.isfinite(value):
        return False
    if minimum is not None and value<minimum:
        return False
    if maximum is not None and value>maximum:
        return False
    return True


def env_int(
    *names:str,default:int,
    minimum:Optional[int]=None,maximum:Optional[int]=None,
)->int:
    """把环境变量里的文本转成整数"""
    value=_env_first(*names)
    if value is None:
        return default
    try:
        parsed=float(value)  #先f再i，因为3.0的配置也能当整数用
        integer=int(parsed)
    except(TypeError,ValueError,OverflowError):
        return default
    if not _in_range(parsed,minimum=minimum,maximum=maximum):
        return default
    return integer

def env_float(
    *names:str,default:float,
    minimum:Optional[float]=None,maximum:Optional[float]=None,
)->float:
    """把环境变量值转成浮点数"""
    value=_env_first(*names)  #取第一个非空值
    if value is None:
        return default
    try:
        parsed=float(value)  #float()遇到非数字内容会报错
    except(TypeError,ValueError,OverflowError):
        return default
    return parsed if _in_range(parsed,minimum=minimum,maximum=maximum) else default


def env_bool(*names:str,default:bool=False)->bool:
    value=_env_first(*names)
    if value is None:
        return default
    normalized=value.strip().lower()
    if normalized in("1","true","yes","on"):
        return True
    if normalized in("0","false","no","off"):
        return False
    return default


def env_path(*names:str,default:Path)->Path:
    """把环境变量里的路径文本转成'Path'对象"""
    value=_env_first(*names)  #先取出原始路径文本
    return Path(value).expanduser() if value else default

#vendor依赖目录
def ensure_deps_on_path(deps_dir:Optional[Path]=None)->Optional[Path]:
    """把vendor依赖目录插到'sys.path'最前面，存在则返回该目录，保证vendor内的版本优先，"""
    deps_dir=deps_dir or (PROJECT_ROOT/".deps")
    if deps_dir.is_dir():
        resolved=str(deps_dir)
        if resolved not in sys.path:
            sys.path.insert(0,resolved)
        return deps_dir
    return None

_NUMERIC_BOUNDS={
    "llm_temperature":(0.0,2.0),
    "llm_timeout":(1.0,3600.0),
    "llm_max_retries":(0,20),
    "llm_max_concurrency":(1,64),
    "llm_max_input_chars":(256,10_000_000),
    "http_timeout":(1.0,3600.0),
    "http_max_retries":(0,20),
    "http_backoff_base":(0.0,300.0),
    "openalex_rps":(0.01,1000.0),
    "crossref_rps":(0.01,1000.0),
    "arxiv_rps":(0.01,1000.0),
    "s2_rps":(0.01,1000.0),
    "opencli_timeout":(1.0,3600.0),
    "opencli_interactive_timeout":(5.0,120.0),
    "opencli_cache_ttl_seconds":(0,30*24*3600),
    "opencli_circuit_failures":(1,100),
    "opencli_circuit_cooldown_seconds":(0,7*24*3600),
    "opencli_max_candidates_per_site":(1,100),
    "bm25_k1":(0.01,100.0),
    "bm25_b":(0.0,1.0),
    "fingerprint_n":(3,64),
    "rrf_k":(1,100000),
    "vector_dim":(64,4096),
    "vector_top_k":(1,1000),
    "vector_batch_size":(1,256),
    "vector_min_score":(0.0,1.0),
    "vector_trigger_ratio":(0.0,1.0),
    "default_top_k":(1,1000),
    "max_chunk_chars":(100,1_000_000),
    "chunk_overlap_chars":(0,1_000_000),
    "agent_max_rounds":(1,100),
    "agent_observation_chars":(256,1_000_000),
    "trace_max_chars":(1000,1_000_000),
    "streamlit_port":(1,65535),
}


@dataclass
class Settings:
    project_root:Path=PROJECT_ROOT
    data_dir:Path=PROJECT_ROOT/"data"
    deps_dir:Path=PROJECT_ROOT/".deps"
    llm_api_key:str=""  #默认空字符串=没配key
    llm_base_url:str="https://api.deepseek.com"
    llm_model:str="deepseek-chat"
    llm_temperature:float=0.2
    llm_timeout:float=120.0  #单次请求超时秒速
    llm_max_retries:int=3
    llm_max_concurrency:int=4
    llm_max_input_chars:int=48000
    #网络抓取相关配置
    contact_email:str=""
    http_timeout:float=30.0
    http_max_retries:int=4
    http_backoff_base:float=1.5  #重试等待退避基数，每轮等待时间按倍数增长
    openalex_rps:float=8.0
    crossref_rps:float=4.0
    arxiv_rps:float=0.33
    s2_rps:float=1.0
    s2_api_key:str=""
    requests_proxy:str=""  #请求代理地址
    #OpenCLI 浏览器补充源
    opencli_command:str="opencli"
    opencli_profile:str=""
    opencli_timeout:float=12.0
    opencli_interactive_timeout:float=35.0
    opencli_cache_ttl_seconds:int=3600
    opencli_circuit_failures:int=3
    opencli_circuit_cooldown_seconds:int=300
    opencli_max_candidates_per_site:int=4
    bm25_k1:float=1.5
    bm25_b:float=0.75
    fingerprint_n:int=7  #精确指纹的窗口大小
    rrf_k:int=60
    vector_enabled:bool=True
    vector_backend:str="neural"
    vector_dim:int=384
    vector_model_dir:Path=PROJECT_ROOT/"data"/"models"/"paraphrase-multilingual-MiniLM-L12-v2"
    vector_batch_size:int=16
    vector_top_k:int=10
    vector_min_score:float=0.12
    vector_trigger_ratio:float=0.85
    default_top_k:int=10
    max_chunk_chars:int=1800
    chunk_overlap_chars:int=200  #相邻片段重叠多少字
    agent_max_rounds:int=12
    agent_observation_chars:int=4000
    trace_max_chars:int=20000
    #网页端口配置
    streamlit_port:int=8501
    _notes:List[str]=field(default_factory=list,repr=False)

    def __post_init__(self)->None:
        """统一约束数值配置，兼容直接构造 Settings 的调用路径。"""
        for name,(minimum,maximum) in _NUMERIC_BOUNDS.items():
            value=getattr(self,name)
            try:
                numeric=float(value)
            except(TypeError,ValueError,OverflowError):
                numeric=float("nan")
            if not _in_range(numeric,minimum=minimum,maximum=maximum):
                setattr(self,name,getattr(type(self),name))

        #重叠长度必须小于片段上限；若默认重叠也不适用，则压到上限以内。
        if self.chunk_overlap_chars>=self.max_chunk_chars:
            fallback=getattr(type(self),"chunk_overlap_chars")
            self.chunk_overlap_chars=(
                fallback if fallback<self.max_chunk_chars
                else max(0,self.max_chunk_chars-1)
            )

    #派生路径
    @property
    def raw_dir(self)->Path:
        """存放原始数据"""
        return self.data_dir/"raw"
    @property
    def clean_dir(self)->Path:
        """洗后正文存放目录"""
        return self.data_dir/"clean" 
    @property
    def pdf_dir(self)->Path:
        """下载的PDF文件目录"""
        return self.data_dir/"pdfs" 
    @property
    def summary_dir(self)->Path:
        """文献摘要文本目录"""
        return self.data_dir/"summaries" 
    @property
    def cache_dir(self)->Path:
        """缓存目录"""
        return self.data_dir/"cache" 
    @property
    def logs_dir(self)->Path:
        """轮转日志目录。"""
        return self.data_dir/"logs"
    @property
    def db_path(self)->Path:
        """SQLite数据库文件位置"""
        return self.data_dir/"index.db" 
    @property
    def all_dirs(self)->List[Path]: 
        """返回目录路径列表"""
        return [
            self.data_dir,
            self.raw_dir,
            self.clean_dir,
            self.pdf_dir,
            self.summary_dir,
            self.cache_dir,
            self.logs_dir,
        ]  #访问的是属性，不用写括号
    def ensure_dirs(self)->None:
        """创建全部数据目录（幂等）"""
        for path in self.all_dirs:
            path.mkdir(parents=True,exist_ok=True)

    #能力判断
    @property
    def has_llm(self)->bool:
        """判断有没有配大模型密钥"""
        return bool(self.llm_api_key)
    def require_llm(self)->None:  #没配密钥就提示异常，并指导怎么做
        """报错给出的解决方案，当没配key却需要key时调用"""
        if not self.has_llm:
            raise RuntimeError(
                "未配置LLM API Key\n"
                "请设置环境变量LIT_LLM_API_KEY或DEEPSEEK_API_KEY",
                f"例如在{self.project_root/'.env'}中写入：\n"
                "LIT_LLM_API_KEY=sk-**********\n"
                "参考.env.example；检索与引用不受影响"
            )
    def describe(self)->List[Tuple[str,str,str]]:
        """返回[项目，值，状态]供cli.py doctor打印，状态取值'ok','warn''miss''info'"""
        def mask(secret:str)->str:
            """专用小函数，给密钥打码"""
            if not secret:
                return "(未设置)"
            if len(secret)<=10:
                return secret[:2]+"*"*(len(secret)-2)
            return secret[:6]+"..."+"*"*4+"(%d字符)"%len(secret)  #长密钥只显示前6位，中间省略
        rows:List[Tuple[str,str,str]]=[]
        rows.append(("解释器",sys.executable,"info"))
        rows.append(
            ("Python版本","%d.%d.%d"%sys.version_info[:3],  #版本号前三位拼起来
             "ok" if sys.version_info[:2]>=(3,12)else "warn")
        )
        rows.append(("项目根目录",str(self.project_root),"info"))
        rows.append(("数据目录",str(self.data_dir),  #追加数据目录
                     "ok"if self.data_dir.is_dir() else "miss"))
        rows.append(("SQLite索引",str(self.db_path),
                     "ok"if self.db_path.is_file() else "info"))
        deps_ok=self.deps_dir.is_dir()
        rows.append(("vendor依赖目录",str(self.deps_dir)+(""if deps_ok else"（不存在）"),
                     "ok" if deps_ok else "warn"))
        rows.append(("LLM模型",self.llm_model,"info"))
        rows.append(("LLM端点",self.llm_base_url,"info"))
        rows.append(("LLM Key",mask(self.llm_api_key),
                     "ok"if self.has_llm else "miss"))
        opencli_command=(self.opencli_command or "").strip()
        opencli_token=opencli_command.split()[0].strip('"') if opencli_command else ""
        opencli_via_npx=(
            Path(opencli_token).name.lower()=="opencli"
            and bool(shutil.which("npx"))
        )
        opencli_found=bool(
            opencli_token and (
                Path(opencli_token).is_file() or shutil.which(opencli_token)
                or opencli_via_npx
            )
        )
        rows.append(("OpenCLI浏览器补充源",
                     opencli_command+("（固定版本 npx 后备）" if opencli_via_npx
                                      and not shutil.which(opencli_token) else "")
                     if opencli_command else "（未配置命令）",
                     "ok" if opencli_found else "warn"))
        rows.append(("联系邮箱（polite pool）",self.contact_email or "（未设置）",
                    "ok"if self.contact_email else "warn"))
        env_file=find_env_file()  #把实际读到的配置文件地址打出来，排查"改了 .env 却不生效"时一眼能看出放错位置了
        rows.append((".env 文件",str(env_file) if env_file else "未找到（全部使用代码默认值）",
                     "ok" if env_file else "warn"))
        #依赖可用性:检查各个依赖包是否装好
        for module,purpose,required in(
            ("requests","HTTP请求",True),
            ("sqlite3","本地索引",True),
            ("jieba","中文分词",True),
            ("streamlit","网页界面",False),
        ):
            try:
                __import__(module)
                rows.append(("依赖"+module,purpose,"ok"))
            except Exception as exc:
                rows.append(
                    ("依赖"+module,"%s->%s"%(purpose,type(exc).__name__),
                    "miss" if required else "warn")  #必须依赖标为miss
                )
        from litagent.compat import get_pdf_module
        pdf_module=get_pdf_module()
        rows.append(
            ("依赖PyMuPDF","PDF文本抽取(%s)"%getattr(pdf_module,"__name__","?"),  #getattr取不到属性返回默认值?
             "ok" if pdf_module is not None else "miss")
        )
        return rows

    def missing_required_deps(self)->List[str]:
        """返回缺失的必须依赖模块名列表"""
        missing=[]
        for module in ("requests","jieba"):
            try:
                __import__(module)  #不关心错误详情，不接异常对象
            except Exception:
                missing.append(module)
        from litagent.compat import get_pdf_module
        if get_pdf_module() is None:  #如果成立，说明没装PyMuPDF
            missing.append("PyMuPDF")
        return missing

#用单例模式，保证全局只有一份配置
def build_settings()->Settings:
    """从环境变量构造Settings(会先加载.env)"""
    load_dotenv(find_env_file() or (PROJECT_ROOT/".env"))  #自动往上找 .env，找不到就按代码目录下的路径兜底
    data_dir=env_path("LIT_DATA_DIR",default=PROJECT_ROOT/"data")  #读取目录LIT_DATA_DIR优先
    return Settings(
        project_root=PROJECT_ROOT,
        data_dir=data_dir,
        deps_dir=env_path("LIT_DEPS_DIR",default=PROJECT_ROOT/".deps"),
        llm_api_key=env_str("LIT_LLM_API_KEY","DEEPSEEK_API_KEY","OPENAI_API_KEY"),
        llm_base_url=env_str("LIT_LLM_BASE_URL","DEEPSEEK_BASE_URL",
                             default="https://api.deepseek.com"),
        llm_model=env_str("LIT_LLM_MODEL",default="deepseek-chat"),
        llm_temperature=env_float(
            "LIT_LLM_TEMPERATURE",default=0.2,minimum=0.0,maximum=2.0
        ),
        llm_timeout=env_float(
            "LIT_LLM_TIMEOUT",default=120.0,minimum=1.0,maximum=3600.0
        ),
        llm_max_retries=env_int(
            "LIT_LLM_MAX_RETRIES",default=3,minimum=0,maximum=20
        ),
        llm_max_concurrency=env_int(
            "LIT_LLM_MAX_CONCURRENCY",default=4,minimum=1,maximum=64
        ),
        llm_max_input_chars=env_int(
            "LIT_LLM_MAX_INPUT_CHARS",default=48000,
            minimum=256,maximum=10_000_000,
        ),
        contact_email=env_str("LIT_CONTACT_EMAIL","CROSSREF_MAILTO",
                              "OPENALEX_MAILTO"),
        http_timeout=env_float(
            "LIT_HTTP_TIMEOUT",default=30.0,minimum=1.0,maximum=3600.0
        ),
        http_max_retries=env_int(
            "LIT_HTTP_MAX_RETRIES",default=4,minimum=0,maximum=20
        ),
        http_backoff_base=env_float(
            "LIT_HTTP_BACKOFF_BASE",default=1.5,minimum=0.0,maximum=300.0
        ),
        openalex_rps=env_float(
            "LIT_OPENALEX_RPS",default=8.0,minimum=0.01,maximum=1000.0
        ),
        crossref_rps=env_float(
            "LIT_CROSSREF_RPS",default=4.0,minimum=0.01,maximum=1000.0
        ),
        arxiv_rps=env_float(
            "LIT_ARXIV_RPS",default=0.33,minimum=0.01,maximum=1000.0
        ),
        s2_rps=env_float(
            "LIT_S2_RPS",default=1.0,minimum=0.01,maximum=1000.0
        ),
        s2_api_key=env_str("LIT_S2_API_KEY","SEMANTIC_SCHOLAR_API_KEY"),
        requests_proxy=env_str("LIT_PROXY","HTTPS_PROXY","https_proxy"),
        opencli_command=env_str("LIT_OPENCLI_COMMAND",default="opencli"),
        opencli_profile=env_str("LIT_OPENCLI_PROFILE","OPENCLI_PROFILE"),
        opencli_timeout=env_float(
            "LIT_OPENCLI_TIMEOUT",default=12.0,minimum=1.0,maximum=3600.0
        ),
        opencli_interactive_timeout=env_float(
            "LIT_OPENCLI_INTERACTIVE_TIMEOUT",default=35.0,
            minimum=5.0,maximum=120.0,
        ),
        opencli_cache_ttl_seconds=env_int(
            "LIT_OPENCLI_CACHE_TTL_SECONDS",default=3600,
            minimum=0,maximum=30*24*3600,
        ),
        opencli_circuit_failures=env_int(
            "LIT_OPENCLI_CIRCUIT_FAILURES",default=3,minimum=1,maximum=100
        ),
        opencli_circuit_cooldown_seconds=env_int(
            "LIT_OPENCLI_CIRCUIT_COOLDOWN_SECONDS",default=300,
            minimum=0,maximum=7*24*3600,
        ),
        opencli_max_candidates_per_site=env_int(
            "LIT_OPENCLI_MAX_CANDIDATES_PER_SITE",default=4,
            minimum=1,maximum=100,
        ),
        bm25_k1=env_float(
            "LIT_BM25_K1",default=1.5,minimum=0.01,maximum=100.0
        ),
        bm25_b=env_float(
            "LIT_BM25_B",default=0.75,minimum=0.0,maximum=1.0
        ),
        fingerprint_n=env_int(
            "LIT_FINGERPRINT_N",default=7,minimum=3,maximum=64
        ),
        rrf_k=env_int("LIT_RRF_K",default=60,minimum=1,maximum=100000),
        vector_enabled=env_bool("LIT_VECTOR_ENABLED",default=True),
        vector_backend=env_str(
            "LIT_VECTOR_BACKEND",default="neural"
        ).strip().lower(),
        vector_dim=env_int(
            "LIT_VECTOR_DIM",default=384,minimum=64,maximum=4096
        ),
        vector_model_dir=env_path(
            "LIT_VECTOR_MODEL_DIR",
            default=PROJECT_ROOT/"data"/"models"/"paraphrase-multilingual-MiniLM-L12-v2",
        ),
        vector_batch_size=env_int(
            "LIT_VECTOR_BATCH_SIZE",default=16,minimum=1,maximum=256
        ),
        vector_top_k=env_int(
            "LIT_VECTOR_TOP_K",default=10,minimum=1,maximum=1000
        ),
        vector_min_score=env_float(
            "LIT_VECTOR_MIN_SCORE",default=0.12,minimum=0.0,maximum=1.0
        ),
        vector_trigger_ratio=env_float(
            "LIT_VECTOR_TRIGGER_RATIO",default=0.85,
            minimum=0.0,maximum=1.0,
        ),
        default_top_k=env_int("LIT_TOP_K",default=10,minimum=1,maximum=1000),
        max_chunk_chars=env_int(
            "LIT_MAX_CHUNK_CHARS",default=1800,
            minimum=100,maximum=1_000_000,
        ),
        chunk_overlap_chars=env_int(
            "LIT_CHUNK_OVERLAP_CHARS",default=200,
            minimum=0,maximum=1_000_000,
        ),
        agent_max_rounds=env_int(
            "LIT_AGENT_MAX_ROUNDS",default=12,minimum=1,maximum=100
        ),
        agent_observation_chars=env_int(
            "LIT_AGENT_OBSERVATION_CHARS",default=4000,
            minimum=256,maximum=1_000_000,
        ),
        trace_max_chars=env_int(
            "LIT_TRACE_MAX_CHARS",default=20000,
            minimum=1000,maximum=1_000_000,
        ),
        streamlit_port=env_int(
            "LIT_STREAMLIT_PORT",default=8501,minimum=1,maximum=65535
        ),
    )

_settings:Optional[Settings]=None

def get_settings(reload:bool=False)->Settings:
    """获取全局配置单例，当reload=True时重新读取环境变量"""
    global _settings
    if _settings is None or reload:
        _settings=build_settings()
    return _settings
settings:Settings=get_settings()
ensure_deps_on_path(settings.deps_dir)

__all__=[
    "Settings",  #导出配置类Setting
    "settings",
    "get_settings",
    "build_settings",
    "load_dotenv",  #导出.env加载函数
    "find_env_file",  #导出.env查找函数，自检与界面要报告实际生效的配置文件
    "ensure_deps_on_path",
    "env_str",
    "env_int",  #导出整数取值助手
    "env_float",
    "env_path",
    "PROJECT_ROOT",  #导出项目根目录
    "PACKAGE_DIR",
]
