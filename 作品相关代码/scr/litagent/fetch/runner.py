"""抓取文献，去重后写入本地库。

入库前依次比较 DOI、归一化标题和标题相似度。原始结果另存为 JSONL，
方便断网重放。查询进度写入 ingest_log，可以从中断处继续。单次请求
失败只记入报告，其他查询照常执行。
"""
from __future__ import annotations  #'annotations'让类型标注延迟求值：Py3.8 也能写'list[str]'这种新语法

import json  #'json'用于归档的 JSONL 读写，每行一条记录，追加写不会互相污染
import time  #'time'只取两样东西：'monotonic'算耗时，'strftime'给归档文件打时间戳
from dataclasses import dataclass,field  #'dataclass'是为了少写一堆样板，'field'专门用来给可变字段配工厂函数
from pathlib import Path  #'Path'全程走运算符拼接（'/'），不做字符串拼接，免得踩 Windows 反斜杠
from typing import Any,Dict,Iterable,List,Optional,Sequence,Tuple  #类型名集中导入，函数签名因此能写全，读代码时不用回翻定义

from litagent.config import Settings,settings as default_settings  #配置走单例导入并另起别名，测试里可以整个替换掉，不必改全局状态
from litagent.fetch.base import Source,get_source  #'get_source'是数据源工厂，调度器只认'Source'这个抽象接口不认具体实现
from litagent.index.store import Store  #'Store'是唯一落盘入口，调度器自己不写一句 SQL
from litagent.schema import(  #领域模型与归一化函数统一从'schema'导入，保证两边口径完全一致
    Document,
    normalize_doi,
    normalize_title,
    title_similarity,
    utcnow,
)

#标题判重的默认阈值。0.92 是经验值：能滤掉「同文不同源」的重复，
#又不会把「同一课题的不同论文」误判成一篇
DEFAULT_TITLE_THRESHOLD=0.92


def _slugify(text:str,max_length:int=48)->str:
    """把查询词变成安全的文件名片段。"""
    pieces:List[str]=[]
    for char in text.strip():
        if char.isalnum() or char in "-_":
            pieces.append(char)
        elif char in " \t/\\:;,.|":
            pieces.append("_")

    slug="_".join(part for part in "".join(pieces).split("_") if part)
    return (slug or "query")[:max_length]


@dataclass
class FetchReport:  #一次抓取任务的执行报告，字段全是计数器，最后统一渲染成人读的文本
    """一次抓取任务的执行报告。"""

    source:str=""  #数据源名，构造时就要给，报告里到处都要引用
    queries:List[str]=field(default_factory=list)  #'default_factory'而不是可变默认值，让每个实例各有一份新列表避免共享
    fetched:int=0  #抓到多少条（含重复和空记录），是后面各项的分母
    new:int=0  #真正新增入库的条数
    merged:int=0  #与库中已有记录合并的条数
    skipped:int=0  #因标题或内容为空被丢弃的条数
    failed_queries:List[str]=field(default_factory=list)  #失败的查询词单独记账，成功跑完的不会混进来
    errors:List[str]=field(default_factory=list)  #错误样例只留少量几条，全量堆进报告会把终端刷屏
    pdfs_downloaded:int=0  #PDF 下载成功数，与入库计数分开统计，两者口径不同不能混算
    pdf_failures:int=0  #PDF 下载失败数，不阻断入库流程，只在报告里体现
    archive_files:List[str]=field(default_factory=list)  #本次写出的归档文件路径列表，便于出错后回溯
    elapsed_seconds:float=0.0  #总耗时用浮点秒，渲染时再统一格式化，中间不做取整

    def summary(self)->str:
        lines=[
            "抓取报告 —— 数据源 %s"%self.source,  #标题行带上数据源名，一次跑多个源时日志里分得清是哪一段
            "  查询数量   : %d（失败 %d）"%(len(self.queries),len(self.failed_queries)),  #查询数量后面紧跟失败数，一眼看出这轮跑得顺不顺
            "  抓到条目   : %d"%self.fetched,  #抓到条目是入库前的原始计数，和下面的新增数对照着看才有意义
            "  新增入库   : %d"%self.new,  #统计三个去向：新增、合并、跳过，三者相加应等于抓到的有效条目
            "  合并去重   : %d"%self.merged,  #合并去重数是双重去重真正生效的量，为 0 说明几乎全是新文献
            "  跳过       : %d"%self.skipped,  #跳过通常来自空标题或空正文，量大了要去查数据源的质量
            "  PDF 下载   : 成功 %d，失败 %d"%(self.pdfs_downloaded,self.pdf_failures),  #'PDF 下载'单独一行，成功与失败并排写，避免看漏失败
            "  耗时       : %.1f 秒"%self.elapsed_seconds,  #耗时保留一位小数就够，精度再高对排查也没有帮助
        ]
        if self.archive_files:  #没有归档文件时整行省掉，免得报告里挂个恒为 0 的空字段
            lines.append("  原始归档   : %d 个文件"%len(self.archive_files))  #归档是按查询词分批写的，所以这里报的是文件个数不是条目数
        if self.failed_queries:  #失败查询可能很多，只列前 5 条，后面的看日志
            lines.append("  失败查询   : %s"%", ".join(self.failed_queries[:5]))  #用顿号连接而不是换行，失败列表短的时候一行就能看完
        if self.errors:  #错误样例单独起一段，正文过长时缩进两层便于和上面区分
            lines.append("  错误样例   :")
            for error in self.errors[:5]:  #同样只取前 5 条，重复的报错看多了没有信息量
                lines.append("    - "+error)  #每条错误前面加短横线，多行时视觉上自然成列表
        return "\n".join(lines)  #'join'成单串交给调用方，需要落盘时直接写文件

    def to_dict(self)->Dict[str,Any]:  #转成可序列化的字典，给 JSON 落盘和上游程序消费用，字段名与属性一一对应
        return{  #字典的键与'FetchReport'的字段同名，来回转换不会出现对不上的情况
            "source":self.source,
            "queries":self.queries,
            "fetched":self.fetched,
            "new":self.new,
            "merged":self.merged,  #失败查询原样带出，前面的截断只影响展示不影响这里的数据
            "skipped":self.skipped,
            "failed_queries":self.failed_queries,  #错误样例同样带全量，报告只是渲染时截断，数据本身不丢
            "errors":self.errors,
            "pdfs_downloaded":self.pdfs_downloaded,
            "pdf_failures":self.pdf_failures,
            "archive_files":self.archive_files,
            "elapsed_seconds":round(self.elapsed_seconds,2),  #耗时在对外输出时才保留两位小数，内部仍然存全精度
        }


class FetchRunner:  #抓取调度器，串起去重、落库、归档和续传四件事
    """抓取调度器。"""  #唯一职责是把各数据源的产出汇总入库，自己不做网络请求

    def __init__(self,store:Store,settings:Optional[Settings]=None)->None:  #'store'位置必传，'settings'可选，测试里可以塞一份临时配置进来
        self.store=store  #存一份'Store'引用，入库全部经由它，调度器不直接碰数据库
        self.settings=settings or default_settings  #'settings'传空就落到全局默认配置上，命令行入口因此可以更省事
        self.settings.ensure_dirs()  #构造时就建好目录，避免真正写归档时才发现路径不存在

    #分隔线：下面这一节是现有语料去重索引
    #和上下文共用同一张映射表，先把库里的存量读进内存
    # ------------------------------------------------------------------
    def _load_existing_maps(self)->Tuple[Dict[str,str],Dict[str,str]]:  #返回两张映射表，调用方按顺序解包，两边顺序不能调换
        """载入库内已有的'{doi: doc_id}'与'{title_norm: doc_id}'两张映射表。

        个人规模（几千篇）下整表载入内存完全可接受，比每条新文献都去查库快得多。
        两张表用'setdefault'填充，同一键撞车时保留最早那条，保证结果可复现。

        """
        doi_map:Dict[str,str]={}
        title_map:Dict[str,str]={}  #标题表与 DOI 表分开存，因为标题判据只在 DOI 缺失时才会用到
        rows=self.store.connect().execute(  #一次性把 id、doi 和归一化标题三列全捞出来，避免后续按条查库
            "SELECT id, doi, title_norm FROM documents"  #只取这三列：全文和摘要体积大，去重根本用不到，白读一遍纯浪费内存
        ).fetchall()  #游标读完即弃，几百到几千行的规模没必要维持长连接
        for row in rows:
            norm_doi=normalize_doi(row["doi"])  #读出来先归一化：库里存的可能还是旧格式，直接比会漏判
            if norm_doi:  #DOI 为空或归一化后为空串就跳过，不让空键污染映射表
                doi_map.setdefault(norm_doi,row["id"])  #用'setdefault'只认第一次出现的记录：同一 DOI 撞车时保留最早的，保证结果可复现
            if row["title_norm"]:  #标题为空说明当初没能抽出标题，不影响其他判据，直接跳过
                title_map.setdefault(row["title_norm"],row["id"])  #同理标题也走'setdefault'，先到先得，和 DOI 表保持同一套口径
        return doi_map,title_map  #两张表一起返回，调用方一次解包拿到，不用调两遍函数重复扫表

    def _match_existing(self,doc:Document,doi_map:Dict[str,str],  #判定新文献是否已在库中，命中则返回已有记录的 ID
                        title_map:Dict[str,str])->Optional[str]:  #两张映射表由调用方传入而不是这里现载：同批次多篇文献要反复匹配，载一次就够
        """判断新抓到的文献是否已在库中，返回已存在记录的 ID；库里没有则返回'None'。

        三级判据按「便宜→昂贵」排序：字典命中是常数级，前缀比对要遍历，
        相似度计算最贵，所以前面的能定案就不必付后面的代价。

        """
        norm_doi=normalize_doi(doc.doi)  #第一级判据挑 DOI：它是权威标识，跨源也稳定，命中就当场定案
        if norm_doi and norm_doi in doi_map:  #归一化后精确比对，比大小写和前缀差异都不放过；命中即早退，不再走后面的贵判据
            return doi_map[norm_doi]  #映射里存的是归一化 DOI 到 doc_id 的对应，取出来就是库里的主键

        norm_title=normalize_title(doc.title)  #第一级没命中才轮到标题，先把标题归一化，去掉大小写标点和空白差异
        if not norm_title:  #标题都没有就没法再做判重，早退返回'None'，让它按新文档处理
            return None  #第二级判据是精确串命中：归一化后完全一致的标题认定为同一篇
        if norm_title in title_map:  #字典命中是常数级开销，比算相似度便宜几个数量级，能定案就不必再算
            return title_map[norm_title]  #注意这里比的是归一化标题，不是原始标题，否则大小写不同就会漏判

        #第三级判据做模糊匹配，先看注释再往下读
        prefix=norm_title[:8]  #取前 8 个字符当前缀键：标题开头通常是最有辨识度的部分（冠词除外）
        for existing_norm,doc_id in title_map.items():  #遍历标题表逐条比对，'prefix'切出来的短串比完整标题比对便宜得多
            if not existing_norm.startswith(prefix):  #'startswith'是极廉价的粗筛：前缀都不同说明开头就差很远，相似度不可能够阈值
                continue  #粗筛通过才付相似度的代价，这一步才真正算 3-gram 的重叠程度
            if title_similarity(norm_title,existing_norm)>=DEFAULT_TITLE_THRESHOLD:  #达到阈值即认定为同一篇，返回库中已有记录的 ID
                return doc_id  #整表扫完都没有够格的，才算真正的新文献
        return None  #没有命中返回'None'，调用方据此走新增分支

    #分隔线：下面这一节是入库主流程
    #入库是本模块最重的一步，去重、合并、写全文都在这里收口
    # ------------------------------------------------------------------
    def ingest(self,documents:Iterable[Document],*,
               report:Optional[FetchReport]=None)->FetchReport:  #'report'用关键字传入，不传就现建一个，调用方想省事时可以只关心返回值
        """把一批 Document 去重后写入库，返回（或就地更新）'FetchReport'。

        实现上有一个关键细节：本批内尚未写库的新文档也参与后续去重匹配。
        早期版本在匹配到一条「本批次刚确定要新增、但还没落库」的记录时，
        会去'store.get_document()'查——那当然查不到（还没写），于是
        把重复项当成新文档又插了一条。后果是一批结果里若同时出现预印本与
        正式版（DOI 不同、标题相同），就会双双入库，去重形同虚设。

        现在用一个'pending'映射保存待写入的文档，匹配优先在内存里完成。

        """
        report=report or FetchReport()  #'report'为假值时现建一个，避免在签名里写可变默认值
        doi_map,title_map=self._load_existing_maps()  #两张映射表在本批开始时载一次，批内所有文献共用，不用每条都查库
        now=utcnow()  #时间戳取一次就固定，同批文献的抓取时间因此完全一致，便于按批追溯

        to_insert:List[Document]=[]  #收集待插入的新文档，攒够一批再写库，减少事务次数
        to_update:List[Tuple[str,Document]]=[]  #收集待更新的已有文档，和新增分开走，因为落库方式不同
        #分隔说明：下面是本批次的内存待写记录
        pending:Dict[str,Document]={}  #'pending'解决的就是上面说的那个 bug：它让未落库的文档也能被匹配到

        for doc in documents:
            report.fetched+=1  #抓到的总数先加一，不管后面是入库还是跳过，它是各项统计的分母
            if doc.is_empty():  #空文档（无标题且无正文）没有价值，直接丢弃不让它污染库
                report.skipped+=1  #跳过也计数，跑完报告里能看出数据源的质量
                continue  #抓取时间只在为空时补上，数据源自己带的时间戳优先，不要覆盖
            doc.fetched_at=doc.fetched_at or now
  #三级判据查一遍，命中说明这篇已经在库里（或在本批的'pending'里）
            existing_id=self._match_existing(doc,doi_map,title_map)
            if existing_id:  #注意顺序：先查内存'pending'再查库，反了就会重现上面那个 bug
                #命中时先看是不是本批次刚新增的，是的话直接拿内存里那份
                current=pending.get(existing_id)  #内存里没有才去库里取，这一步才真正付出一次数据库查询
                if current is None:
                    current=self.store.get_document(existing_id,with_fulltext=False)  #'with_fulltext=False'只要元数据：合并用的是标题作者等字段，正文体积大且用不上

                if current is not None:  #确实取到了对象，说明两边都有这条记录，走合并分支
                    # 已存在：补齐缺失字段（如 Crossref 有卷期页、OpenAlex 有摘要）
                    current.merge(doc)  #合并是字段级补齐而不是整条覆盖：Crossref 有卷期页，OpenAlex 有摘要，各取所长
                    if existing_id not in pending:  #还没登记过更新任务的才登记一次，同一个 ID 重复登记会在落库时白写两遍
                        to_update.append((existing_id,current))  #已经登记过的只累加合并计数，不再重复进'to_update'
                    report.merged+=1  #合并计数加一，报告里的合并数就是这么累出来的
                    # 把合并后新学到的 DOI / 标题登记进映射，
                    #补登记这一段的用意写在下两行
                    norm_doi=normalize_doi(current.doi)  #把合并后新学到的 DOI 登记进映射，供本批后续文献匹配
                    if norm_doi:  #新学到的 DOI 可能补上了原来的空缺，此时必须让后续文献也能命中
                        doi_map.setdefault(norm_doi,existing_id)  #用'setdefault'而不覆盖，保留最早的归属，避免把已有记录改指到别处
                    norm_title=normalize_title(current.title)  #标题同理，合并可能让原本空的标题变得有值
                    if norm_title:  #标题也只在原来没有时才登记
                        title_map.setdefault(norm_title,existing_id)  #登记完这条文献就算处理完了，早退进入下一条
                    continue

                #兜底分支：映射指向了一条已经不在库里的记录
                #典型场景是记录被'delete_document'删过，但本次映射是批前载入的，还停在旧快照上
                report.errors.append(  #这种情况当作新文档插入，而不是丢掉这条刚抓到的数据
                    "映射指向的记录不存在，按新增处理：%s -> %s"%(existing_id,doc.id)  #记一条错误进报告，让用户知道映射与库已经不一致了
                )  #写清是哪条映射指向了哪个不存在的 ID，方便定位是删库还是并发写导致

            to_insert.append(doc)  #走到这里说明确属新文献，进入待插入队列
            pending[doc.id]=doc  #同时写进'pending'内存映射，本批后续文献靠它才能匹配到这条还没落库的记录
            report.new+=1  #新增计数加一
            norm_doi=normalize_doi(doc.doi)  #新文档的 DOI 也登记进映射，好让后面抓到的同 DOI 文献能命中
            if norm_doi:
                doi_map[norm_doi]=doc.id  #这里用赋值而不是'setdefault'：新文献的 DOI 必然是新键，不存在旧值要保
            norm_title=normalize_title(doc.title)  #标题同理登记进标题表
            if norm_title:
                title_map[norm_title]=doc.id  #同样直接赋值，本批内后出现的同标题文献会合并到这一条上

        if to_insert:  #有新增才写库，没有就跳过整次写操作，空列表写入纯属浪费
            self.store.upsert_documents(to_insert)  #整批一次'upsert'，比逐条写库少很多事务开销
        if to_update:  #更新与新增分两次写，虽然同为'upsert'但语义不同，日志里好区分
            self.store.upsert_documents([doc for _,doc in to_update])  #更新列表存的是（ID，文档）元组，这里解包取出文档本身

        #正文落库单独走一遍：元数据先写，全文再补
        for doc in to_insert:  #'to_insert'里的文档正文直接落库，这里也包含被合并进来的内容
            if(doc.fulltext or "").strip():  #只有正文非空才写，空串写进去等于用空值覆盖了已有正文
                self.store.set_fulltext(doc.id,doc.fulltext or "")
        for _,doc in to_update:  #'to_update'里的文档同样补写正文，合并进来的全文不能漏
            if(doc.fulltext or "").strip():  #合并后的文档可能从别处拿到了正文，条件判断与新增那边保持一致
                self.store.set_fulltext(doc.id,doc.fulltext or "")

        return report  #报告就地更新并返回，两种用法都能拿到结果，也方便链式调用

    #分隔线：下面这一节是原始数据归档
    #归档的目的是留底：入库逻辑将来改了，还能拿原始数据重跑而不用重新联网
    # ------------------------------------------------------------------
    def archive_raw(self,source:str,query:str,documents:Sequence[Document])->Path:
        """把原始抓取结果写成 JSONL 存档，返回文件路径。"""  #把原始抓取结果写成 JSONL 存档，返回文件路径
        directory=self.settings.raw_dir/source
        directory.mkdir(parents=True,exist_ok=True)  #'parents=True'一次补齐中间层，'exist_ok=True'让重复归档不报错
        stamp=time.strftime("%Y%m%d-%H%M%S")  #时间戳精确到秒，同名查询短时间重复跑也不会互相覆盖
        path=directory/("%s-%s.jsonl"%(_slugify(query),stamp))  #文件名带上洗过的查询词，出问题时一眼看出是哪次查询的产物
        with open(path,"w",encoding="utf-8") as handle:  #落盘统一用 UTF-8：中文标题不转义也不乱码，换台机器读回来一致
            for doc in documents:
                handle.write(json.dumps(doc.to_dict(),ensure_ascii=False)+"\n")  #'ensure_ascii=False'保住中文可读性，'\n'收尾保证追加写时行边界不粘连
        return path  #返回路径交给调用方登记进报告，便于事后回溯

    def load_archive(self,path:Path)->List[Document]:  #读回 JSONL 存档，可用来在不联网的情况下重跑清洗和索引
        """读回 JSONL 存档。可用来在不联网的情况下重跑清洗/索引。"""
        documents:List[Document]=[]
        with open(path,"r",encoding="utf-8") as handle:
            for line in handle:
                line=line.strip()  #跳过空行：文件末尾常有一个多余换行，不跳过会解析报错
                if not line:
                    continue
                try:  #单行解析失败只丢这一行，其余数据继续读，不让一处坏数据毁掉整份存档
                    documents.append(Document.from_dict(json.loads(line)))  #异常只捕'ValueError'和'TypeError'：分别是 JSON 格式错和字段类型错
                except(ValueError,TypeError):  #坏行直接跳过，一个坏行换整份存档读不出来是不划算的
                    continue
        return documents  #读到多少返回多少，空文件返回空列表也是合法结果

    #分隔线：下面这一节是在线抓取
    #抓取本身走数据源适配器，这里只负责循环、续传和计数
    # ------------------------------------------------------------------
    def fetch_online(self,source_name:str,queries:Sequence[str],*,
                     limit_per_query:int=25,  #'limit_per_query'默认 25，是单次查询的条数上限，防止一个词就拉回上千条
                     year_from:Optional[int]=None,  #年份下限用'Optional'：不传就是不限年份，和传 0 是两回事
                     year_to:Optional[int]=None,  #年份上限同理，两个边界都给默认值才能放开区间不做限制
                     venue_type:Optional[str]=None,  #会议期刊类型留空表示不限，由各数据源自己解释这个字段
                     open_access_only:bool=False,  #默认关：只抓开放获取会把结果集砍掉一大半，要用户明确要求才开
                     resume:bool=False,  #续传默认关：默认行为应该是从头抓一遍，跳过已有查询是用户显式选择的结果
                     archive:bool=True,  #归档默认开：原始数据是重跑清洗的唯一凭据，默认留底比事后补抓便宜
                     on_progress:Optional[Any]=None,  #进度回调给'Optional'：不传就静默跑，命令行下和库调用下都能用
                     extra_filters:Optional[Dict[str,Any]]=None,  #额外过滤条件透传给数据源适配器，各源支持的字段不同，这里不做统一校验
                     )->FetchReport:
        """从在线源按多个查询词抓取并入库。

        ``resume=True`` 时会跳过 ``ingest_log`` 里已标记完成的查询。
        """
        started=time.monotonic()  #用'monotonic'而不是'time'：它不受系统时钟调整影响，算出的耗时不会为负
        report=FetchReport(source=source_name,queries=list(queries))  #报告里先把数据源和查询词记上，中途抛异常也看得出这轮想跑什么
        source:Source=get_source(source_name,self.settings)  #'get_source'按名字取适配器，调度器不知道也不关心具体是哪个源

        try:
            for query in queries:
                if resume:
                    previous=self.store.get_ingest(source_name,query)
                    if previous and previous.get("status")=="done":
                        if on_progress:
                            on_progress("skip",query,0)
                        continue  #'resume' 命中已完成的查询就直接早退，省掉整轮网络请求

                try:  #整个查询的抓取包在 'try' 里，单点失败不会把整批带崩
                    params:Dict[str,Any]={  #参数字典用显式类型标注，后面 update 追加的过滤器也走同一份
                        "limit":limit_per_query,  #'limit' 是每个查询词各自的上限，不是整批的总上限
                        "year_from":year_from,
                        "year_to":year_to,
                        "venue_type":venue_type,
                        "open_access_only":open_access_only,
                    }
                    if extra_filters:  #额外过滤条件由调用方给，非空才追加，避免把 None 塞进查询参数
                        params.update(extra_filters)

                    documents=source.search(query,**params)  #真正发请求的一行就在这里，异常由外层 'except' 统一接住
                    if not documents:  #空结果要跟「失败」分开记：抓到了但确实没有，是正常结局
                        self.store.mark_ingest(
                            source_name,query,status="empty",count=0,  #状态记成 'empty'，续传时不会被当成未完成而重抓一遍
                            message="无结果",cursor=None,  #条数固定为 0，空结果的语义就是「零条」而不是「没跑」
                        )  #游标写 None，表示这个查询已经没有下一页可续
                        report.failed_queries.append(query)  #无结果也进失败清单，方便事后一眼看出哪个检索词是废的
                        continue

                    if archive:  #'archive' 为假就整段跳过，归档是可选动作，不该拖慢入库
                        path=self.archive_raw(source_name,query,documents)
                        report.archive_files.append(str(path))  #把归档文件路径记进报告，方便事后直接照着路径去翻

                    self.ingest(documents,report=report)  #入库是批量递进去的，去重与合并逻辑都在 'ingest' 内部
                    self.store.mark_ingest(  #入库成功才盖 'done' 章，中途崩了下次续传还会重抓这个查询
                        source_name,query,status="done",count=len(documents),  #记的是抓到的条数而不是新增条数，续传判断只看这个查询跑没跑完
                    )
                    if on_progress:  #有回调才通知，回调是可选的，不传就不打扰调用方
                        on_progress("done",query,len(documents))

                except Exception as exc:  # noqa: BLE001 单个查询失败不中断整批，兜底捕获一切异常：一个查询词炸了，剩下的查询照跑不误
                    message="%s: %s"%(type(exc).__name__,str(exc)[:200])  #只留异常类型和前 200 字，完整堆栈会把报告撑爆
                    report.errors.append("[%s] %s"%(query,message))  #错误文本前面缀上查询词，否则一堆报错分不清是谁的
                    report.failed_queries.append(query)
                    self.store.mark_ingest(  #状态记 'failed'，续传时会被重新捡起来重试
                        source_name,query,status="failed",message=message,  #失败原因一并落库，下次重试前能先看看上次是怎么挂的
                    )
                    if on_progress:  #失败也回调一次，让上层进度条不会停在那里不动
                        on_progress("failed",query,0)
        finally:  #'finally' 保证无论中途抛什么异常，连接都会被释放
            source.close()  #不管成功还是失败都关掉数据源，避免连接泄漏

        #收尾与关源
        report.elapsed_seconds=time.monotonic()-started  #耗时用 'monotonic' 而不是墙上时钟，系统改时间也不会算出负数
        return report

    #本地扫描
    def fetch_local(self,paths:Sequence[Any],*,recursive:bool=True,  #本地扫描入口与在线抓取分开，参数简单得多
                    on_progress:Optional[Any]=None)->FetchReport:
        """扫描本地文献库并入库，返回本次扫描的执行报告

            与在线抓取分开走：本地扫描只认路径，不需要查询词与分页游标。
            
        """
        started=time.monotonic()  #起表时刻在这里取一次，结束再取一次相减就是耗时
        report=FetchReport(source="local",queries=[str(p) for p in paths])  #把路径统一转成字符串写进报告，Path 对象不方便直接打印
        source=get_source("local",self.settings)  #本地源也要走工厂拿，不能直接 new，否则设置注入不进去
        try:  #'try' 包住扫描与入库，任何一步炸了都得走到 'finally' 关源
            documents=source.scan(paths,recursive=recursive,on_progress=on_progress)  # type: ignore[reportAttributeAccessIssue]  #递归开关与进度回调全部透传给数据源，由它决定怎么遍历
            #这里千万别预设 'report.fetched'，'ingest' 会逐条自增
            #预设会让「抓到条目」翻倍，5 个文件显示成 10 条，看着像重复抓
            self.ingest(documents,report=report)  #所有统计都由 'ingest' 内部累加，调用方只负责给报告对象
        finally:  #'finally' 同样兜底关源，扫描抛异常也不会泄漏句柄
            source.close()
        report.elapsed_seconds=time.monotonic()-started  #本地扫描的耗时照旧记进报告，跟在线抓取口径保持一致
        return report

    #残缺题录挑拣
    def find_incomplete(self,*,fields:Sequence[str]=("publisher","venue","pages",  #默认只看这五个字段，不传就按这套约定走
                                                          "volume","year"),
                        require_doi:bool=True,  #默认要求有 DOI，没有 DOI 的文献在线源根本查不到
                        limit:Optional[int]=None)->List[str]:  #'limit' 传 None 表示不截断，把残缺的全捞出来
        """找出题录残缺、值得用在线源补齐的文献 ID，返回按缺的字段数降序排好的 ID

            典型场景有两种：

            - 本地 PDF：只有全文，作者和卷期页全是空的
            - 会议论文：OpenAlex 常常不提供出版者，而 Crossref 有（它是 DOI 注册机构）

            缺得最多的排最前面，传了 'limit' 就只交前几条。
            
        """
        scored:List[Tuple[int,str]]=[]
        for doc in self.store.iter_documents(with_fulltext=False):  #取正文关掉，这里只问题录，把全文读进来纯属浪费内存
            if require_doi and not normalize_doi(doc.doi):
                continue  #没有 DOI 的直接跳过，在线源认 DOI，拿标题去查基本查不准
            missing=sum(1 for f in fields if not getattr(doc,f,None))  #换个写法用 'getattr' 取值，缺字段时返回 None 当成缺
            if missing:  #一个都不缺就不进候选，免得白跑一趟网络
                scored.append((missing,doc.id))
        scored.sort(key=lambda item:(-item[0],item[1]))
        ids=[doc_id for _,doc_id in scored]  #推导式把正文拆掉只留 ID，调用方只关心补哪些
        return ids[:limit] if limit else ids  #truncate，没传 'limit' 就把全部 ID 都交出去

    #类级常量集中列一次，合并时按它逐个字段比对
    BIBLIO_FIELDS=(  #题录字段一律以在线权威源为准，这份清单是合并的白名单
        "title","title_en","doi","year","venue","venue_type","volume",  #写两行只是为了不超列宽，内容上没有区别
        "issue","pages","publisher","publisher_place","edition","isbn",  #卷期页与出版者排在中间，都是 Crossref 最擅长的部分
        "url","language",  #URL 与语种放最后，这两项在线源给的通常也比本地抓的规范
    )  #

    #按 DOI 补题录
    def enrich_by_doi(self,doc_ids:Sequence[str],*,source_name:str="crossref",  #按 DOI 批量补齐题录，是本地 PDF 最常见的补全入口
                      on_progress:Optional[Any]=None)->FetchReport:  #光看名字不知道是给谁补的，文档里要点明场景
        """对已有文献按 DOI 补齐题录，返回本次补全的执行报告

            典型用法：本地 PDF 只有全文、作者和卷期页都缺，用 Crossref 补齐。

            **字段级择优合并**，而不是整体替换：题录类字段以 'crossref' 为准，它是 DOI
            注册机构，刊名卷期页最权威；但内容类字段（摘要、关键词、被引数、全文、PDF
            路径）必须保留原记录里的值，因为 'crossref' 经常完全没有摘要，整体替换会把
            'openalex' 抓来的摘要弄丢。
            
        """
        started=time.monotonic()  #起表时刻只取一次，循环里不再重复取
        report=FetchReport(source=source_name,queries=list(doc_ids))
        source=get_source(source_name,self.settings)  #数据源只取一次复用，不要放进循环里反复建，否则连接会被反复开
        try:  #'try' 罩住整轮循环，中途炸了也能走到 'finally' 把源关掉
            for index,doc_id in enumerate(doc_ids,start=1):  #起止从 1 开始，进度回调里的序号跟人类数数习惯对齐
                doc=self.store.get_document(doc_id,with_fulltext=False)  #取文档时关掉全文，合并只用题录，读全文纯属白费内存
                if doc is None or not doc.doi:  #查不到或没 DOI 都算跳过，不是错误，所以不进错误清单
                    report.skipped+=1  #跳过只累加计数，不写错误，因为这是预期内的正常分支
                    continue  #'continue' 前把计数加好，别把跳过算成失败
                try:
                    found=source.by_doi(doc.doi)  #只按 DOI 精确查，不做标题模糊匹配，避免补错文献
                    if found is None:  #在线源也没有这条就认了，跳过而不是报错
                        report.skipped+=1  #跳过继续下一条，不能因为个别查不到就中断整批
                        continue

                    merged=self._prefer_bibliographic(doc,found,source_name)  #合并交给专门的函数，字段级择优的逻辑集中在一处好维护
                    self.store.upsert_document(merged)  #合并结果整体写回，'upsert' 会按 ID 覆盖而不是插新记录
                    report.merged+=1  #合并成功才计数，写库失败会掉进下面的 'except'
                except Exception as exc:  # noqa: BLE001，单条 DOI 查询失败只累加不中断，跟在线抓取一个路子
                    report.errors.append("%s: %s"%(doc_id,str(exc)[:150]))  #错误里带上文献 ID 并截断长度，方便定位是哪一条
                    report.failed_queries.append(doc_id)  #失败的 ID 也进失败清单，调用方可以只重跑这一批
                if on_progress:  #回调是可选参数，不传就不打扰上层
                    on_progress(index,len(doc_ids),doc_id)  #回调传总进度和当前 ID，界面能直接显示到第几条
        finally:  #'finally' 保证异常时也把源关掉，不会泄漏连接
            source.close()
        report.elapsed_seconds=time.monotonic()-started  #耗时照旧记进报告，跟其它入口的口径保持一致
        return report

    #字段级合并
    @classmethod  #声明成类方法，合并规则要读 'BIBLIO_FIELDS' 但不需要实例状态
    def _prefer_bibliographic(cls,base:Document,incoming:Document,  #第一个参数是旧记录，第二个是在线源查回来的新记录
                              source_name:str)->Document:  #数据源名只用于记账，记下这次补全是谁贡献的
        """把 'incoming' 的题录字段合并进 'base'，保留 'base' 的内容字段

            返回新对象，入参两个都不改；内容类字段只用于补缺，绝不覆盖已有值。
            
        """
        merged=Document.from_dict(base.to_dict())
        merged.id=base.id  #ID 在任何情况下都不能被在线源覆盖，否则索引与引用关系全断

        enriched_fields:List[str]=[]  #记下这次实际被改动的字段名，最后要写进 'extra' 留痕
        for field_name in cls.BIBLIO_FIELDS:
            value=getattr(incoming,field_name,None)  #用 'getattr' 加默认值取，老版本记录缺字段也不会炸
            if value not in(None,"",[]):  #空值不算数：None、空串、空列表都当没值，不能拿它去盖旧的
                if getattr(merged,field_name,None)!=value:  #值真变了才记账，免得每次合并都刷出一堆没意义的字段名
                    enriched_fields.append(field_name)
                setattr(merged,field_name,value)  #写回合并对象，这一步只改拷贝不改原记录

        #作者单拎出来处理，因为在线源给的是结构化姓名，比本地拼的好
        if incoming.authors:  #在线源带了作者才采用，没带就保留原来的，别清空
            merged.authors=incoming.authors  #整体替换作者列表，结构化程度更高，格式也更统一

        #内容类字段反过来：以本地为准，在线的只用来补空缺
        if not(merged.abstract or "").strip() and(incoming.abstract or "").strip():  #条件有两层：本地空的、在线有的，两个都成立才填
            merged.abstract=incoming.abstract  #摘要是典型的只在补缺字段，整体替换会把已有的摘要弄丢
        if not(merged.fulltext or "").strip() and(incoming.fulltext or "").strip():  #全文同理，本地已经抽过全文就不必被在线源覆盖
            merged.fulltext=incoming.fulltext  #只有本地没有全文时才拿在线的顶上

        keywords=list(merged.keywords or[])
        for keyword in incoming.keywords or[]:
            if keyword not in keywords:  #'not in' 判过再追加，去重靠这一句，不要偷懒用集合
                keywords.append(keyword)  #去重后的关键词追加到末尾，不改动原有顺序
        merged.keywords=keywords  #关键词是内容类字段，采用并集而不是择优

        merged.pdf_path=base.pdf_path or incoming.pdf_path  #PDF 路径以本地为准，在线源给的路径多半不可用
        merged.page_count=base.page_count or incoming.page_count  #页数同理，本地解析出来的更可信，在线的只补空缺
        merged.cleaned_at=base.cleaned_at  #清洗时间只有本地有，在线的这条必然是空的，直接沿用
        merged.summarized_at=base.summarized_at  #摘要时间同理直接沿用本地，避免合并把进度标记抹掉
        merged.content_hash=base.content_hash  #内容指纹跟着全文走，全文没换指纹就不能换
        merged.fetched_at=base.fetched_at or incoming.fetched_at  #抓取时间两边都可能有，本地有就用本地的，没有才拿在线的

        counts=[c for c in(base.citation_count,incoming.citation_count)  #被引数可能有一边不是整数，先过筛再比大小
                  if isinstance(c,int)]  #只要 int，排除 None 与字符串，免得 'max' 直接抛类型错误
        merged.citation_count=max(counts) if counts else None  #取两边里大的那个；一个都没有就写 None，别硬编成 0

        #把这轮补全的来源与字段名留在 'extra' 里，方便事后追溯
        extra=dict(incoming.extra or{})
        extra.update(base.extra or{})  #本地的写在后所以优先级更高，在线源不能覆盖本地已有的键
        enriched_from=list(extra.get("enriched_from") or[])  #取出已记录过的来源，是列表就拷一份再改
        if source_name not in enriched_from:  #同一个源重复补过就不重复记，一个源只出现一次
            enriched_from.append(source_name)  #追加来源名，最后在报告里能看出这轮补了几个源
        extra["enriched_from"]=enriched_from  #写回来源列表，保持追加顺序便于回溯先后
        extra["enriched_fields"]=sorted(set(  #这次改动的字段与历史记录取并集，而不是覆盖
            list(extra.get("enriched_fields") or[])+enriched_fields  #旧记录与新记录合并后排序，顺序稳定方便比对与人工核查
        ))  #会改动的几个键都在这里定下来，函数保持零副作用
        merged.extra=extra  #写回 'extra'，整条记录的合并至此收尾

        return merged  #交出新对象，入参两个都没被动过

    #分隔线本身也是注释，留作 PDF 下载这一段的视觉分段
    #下面讲的是下载阶段，跟前面的元数据抓取是两码事
    #同上的收尾分隔线，不改成中文以免破坏原有的排版习惯
    #总时间预算是防死链的闸门，不是限流，按整批算不按单条算
    DEFAULT_DOWNLOAD_BUDGET=900.0  #默认 900 秒，个别死链拖不住整批，超时就带着剩余数提前收工

    #PDF 下载
    def download_pdfs(self,doc_ids:Optional[Sequence[str]]=None,*,  #'doc_ids' 为空表示全库挑，传了就只下这几篇
                      limit:int=50,source_name:Optional[str]=None,  #'limit' 是成功下载的条数上限，失败的不占额度
                      budget_seconds:Optional[float]=None,  #'source_name' 传了就优先用它，它的客户端与限流最可能对得上站点
                      on_progress:Optional[Any]=None)->FetchReport:  #'budget_seconds' 覆盖默认预算，测试与批处理常用
        """下载开放获取 PDF，存到 'data/pdfs'，返回本次下载的执行报告

            这是「本地全文溯源」的原料来源。只下载标记为 OA 且能解析出 PDF 直链的，
            不会尝试绕过付费墙。

            实现上有两处刻意的保守设计（都是踩过坑后加的）：

            1. **一次 URL 只试一次**。早期版本在某条 URL 失败后，会拿其余数据源的 HTTP
               客户端把**同一个 URL** 再试 3 遍。但 URL 打不开与用哪个客户端无关，这只是
               把等待时间乘了 4 倍（每次 4 轮指数退避约 27 秒），几篇死链就能让整批卡住
               十分钟。
            2. **强制校验 PDF 魔数**。元数据里的 'oa_url' 常常指向出版社落地页 HTML，
               不校验就会把 HTML 存成 '.pdf'，后面解析全文时报错很难查。
            
        """
        started=time.monotonic()  #不校验魔数就会把 HTML 存成 .pdf，后面解析全文时报错很难查
        deadline=started+(budget_seconds or self.DEFAULT_DOWNLOAD_BUDGET)  #超时时刻先算出来，循环里每次只做一次比较
        report=FetchReport(source=source_name or "auto",queries=[])  #没给预算就用类常量兜底，调用方不必关心默认值
        sources:Dict[str,Source]={}  #报告里的数据源名留空时写 'auto'，表示由解析函数自己挑源
        candidates:List[Document]=[]  #字节码缓存复用，避免同一条文献反复新建数据源对象
  #候选列表先攒起来再统一排序，边扫边下会让优先级失效
        if doc_ids:  #指定了 ID 就按指定的来，顺序也不动
            candidates.extend(self.store.get_documents(list(doc_ids)).values())  #只取题录不取全文，下载决策用不到正文
        else:  #没指定 ID 就全库扫一遍，自己挑值得下的
            #优先下带 arXiv ID 的，它的 PDF 直链最可靠
            for doc in self.store.iter_documents(limit=None):  #'limit' 传 None 表示不限条数，靠后面的上限截断
                if doc.pdf_path and Path(doc.pdf_path).is_file():  #本地已经有真实 PDF 文件就跳过，别重复下载
                    continue  #文件路径可能已经失效，所以还要 'is_file' 实测一次
                if not doc.is_oa and not(doc.extra or{}).get("arxiv_id"):  #非 OA 又没有 arXiv ID 的直接跳过，这两类都拿不到直链
                    continue  #'continue' 放在这里，跳过的条目连候选都不算
                candidates.append(doc)  #够格进候选了，后面统一排序再决定下载顺序
                if len(candidates)>=limit*4:  #最多只攒上限的四倍，避免全库百万条时把内存吃光
                    break  #攒够了就停，剩下的留到下次调用继续扫
            candidates.sort(  #排序让 arXiv 的排前面，其余 OA 的排后面
                key=lambda d:0 if(d.extra or{}).get("arxiv_id") else 1  #排序键用 0 和 1 当优先级，0 更小所以排得更靠前
            )  #同一优先级内部顺序不动，排序是稳定的

        downloaded=0  #成功下载的计数，跟失败数分开算，失败不占额度
        for position,doc in enumerate(candidates):  #'position' 是候选里的下标，预算超支时要用它算剩余数
            if downloaded>=limit:  #到了上限就停，但已经从库里拿出来的候选不算浪费
                break  #break 而不是 continue，再往后已经没有必要看了
            if time.monotonic()>deadline:  #每次循环都查一次表，网络卡住时也能及时刹车
                report.errors.append(
                    "已达下载时间预算（%.0f 秒），剩余 %d 篇未处理"  #预算用尽时把剩余篇数写进报告，别只是安静地提前收工
                    %(budget_seconds or self.DEFAULT_DOWNLOAD_BUDGET,  #报错里带上实际用的预算值，方便确认是不是配置没生效
                       len(candidates)-position)  #剩余篇数按候选总数减去当前下标算，数字要对得上
                )  #提前退出，剩下的标注在错误清单里由人来决定要不要再跑
                break
            try:  #单篇下载的异常只计失败，绝不让它中断整批
                url,resolved_source=self._resolve_pdf_url(doc,sources,source_name)  #先解析出直链和产出它的源名，源名决定用哪个 HTTP 客户端
                if not url:  #解析不出直链就当没这条，跳过而不是报错
                    continue  #'continue' 前不计数，无直链既不算成功也不算失败
                dest=self.settings.pdf_dir/(_slugify(doc.id,96)+".pdf")  #文件名用文献 ID 做安全化切片，96 是长度上限，避开超长文件名
                sources[resolved_source].client.download(url,dest,require_pdf=True)  #用产出该 URL 的那个源的客户端下载，限流与 UA 才对得上站点
                doc.pdf_path=str(dest)  #'require_pdf' 打开魔数校验，不是 PDF 就直接抛异常不落盘
                doc.is_oa=True  #下载成功才写路径，写早了会留下指向不存在文件的记录
                doc.extra=dict(doc.extra or{})  #顺手标记为 OA，既然下到了 PDF 就说明它是开放获取的
                doc.extra["pdf_source"]=resolved_source
                doc.extra["pdf_url_used"]=url  #记下 PDF 是从哪个源拿的，将来换源时能对得上
                self.store.upsert_document(doc)  #用过的直链也留着，出问题能直接点开这个 URL 复核
                downloaded+=1  #落库，让这次下载的路径与来源对后续步骤可见
                report.pdfs_downloaded+=1  #成功计数在写库之后才加，写库失败会掉进下面的 except
            except Exception as exc:  # noqa: BLE001，单篇失败只累加不抛出，跟在线抓取一样先隔离开
                report.pdf_failures+=1  #失败计数单独一列，成功失败在报告里一眼能分辨
                report.errors.append("%s: %s"%(doc.short_ref(),str(exc)[:150]))  #错误里带短引用与详细信息，半天抓不到是哪一篇时能救命
            if on_progress:  #有回调才通知，回调是可选的
                on_progress(downloaded,limit,doc.title[:60])  #回调里给的是成功数与上限，不是已处理的候选下标

        report.elapsed_seconds=time.monotonic()-started  #耗时照旧记进报告，跟其它入口一个口径
        for source in sources.values():  #循环结束后统一关源，不能像单源那样在循环里关
            source.close()  #缓存里每个打开过的源都要关，漏一个就泄漏一个连接
        return report  #报告交回调用方，成功失败都在里面

    #挑源与直链
    def _resolve_pdf_url(self,doc:Document,cache:Dict[str,Source],  #返回直链与产出它的源名，源名必须一起返回
                         preferred:Optional[str])->Tuple[Optional[str],str]:  #'cache' 是跨篇复用的数据源字典，避免同一次任务反复建源
        """找出该文献可用的 PDF 直链，返回 'url' 与数据源名组成的二元组

            数据源名很重要：下载要用**产出该 URL 的那个源**的 HTTP 客户端，这样限流与
            UA 才是对那个站点的（'arxiv' 要求 3 秒间隔，用它去下别的站点会白白慢 3 倍）。
            
        """
        order:List[str]=[]
        for name in(preferred,"arxiv","openalex","semanticscholar","crossref"):  #其余按可靠性固定排序，双保险写法不怕传进来的名字是空
            if name and name not in order:  #'if name' 顺带滤掉 None 与空串，空名字进列表没有意义
                order.append(name)  #判重后追加，同一个源不重复试，试几次结果都一样

        for name in order:
            try:  #单个源解析失败只跳过，不能让它中断整个解析
                if name not in cache:  #只在缓存里没有时才新建，复用是这里的性能关键
                    cache[name]=get_source(name,self.settings)  #'get_source' 走工厂拿，保证设置与限流参数都注入进去
                url=cache[name].pdf_url(doc)  #让数据源自己从元数据里挖直链，挖不到就返回空
                if url:  #拿到直链就立刻返回，源名一起交出去供下载阶段使用
                    return url,name  #直链与源名一起返回，二者必须配对，不能只返回 URL
            except Exception:  # noqa: BLE001，解析异常当没有处理，换下一个源接着试
                continue  #换下一个源继续，一个源不认识这条文献很正常
        return None,""  #全试完都没有就交空值，调用方按「无直链」处理

  #文件结尾留两个空行，符合代码检查工具的排版要求
__all__=["FetchRunner","FetchReport","DEFAULT_TITLE_THRESHOLD"]  #明确声明对外导出的三个名字，别的地方只该用这三个
