"""用 SQLite 保存文献、片段、索引、总结和缓存。"""
from __future__ import annotations
#延迟注解与标准库依赖

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any,Dict,Iterable,Iterator,List,Optional,Sequence,Tuple

from litagent.schema import(
    Author,
    Chunk,
    Claim,
    Document,
    Summary,
    normalize_doi,
    normalize_title,
    title_similarity,
    utcnow,
)

SCHEMA_VERSION="5"

#建表语句：一个模块级常量
SCHEMA_SQL="""
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA temp_store=MEMORY;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS documents (
    id                TEXT PRIMARY KEY,
    doi               TEXT,
    title             TEXT NOT NULL DEFAULT '',
    title_en          TEXT,
    authors_json      TEXT NOT NULL DEFAULT '[]',
    year              INTEGER,
    venue             TEXT,
    venue_type        TEXT NOT NULL DEFAULT 'unknown',
    volume            TEXT,
    issue             TEXT,
    pages             TEXT,
    publisher         TEXT,
    publisher_place   TEXT,
    edition           TEXT,
    isbn              TEXT,
    url               TEXT,
    access_date       TEXT,
    language          TEXT NOT NULL DEFAULT 'en',
    abstract          TEXT,
    keywords_json     TEXT NOT NULL DEFAULT '[]',
    source            TEXT NOT NULL DEFAULT 'unknown',
    source_id         TEXT,
    license           TEXT,
    is_oa             INTEGER NOT NULL DEFAULT 0,
    pdf_path          TEXT,
    page_count        INTEGER,
    citation_count    INTEGER,
    references_json   TEXT NOT NULL DEFAULT '[]',
    content_hash      TEXT,
    fetched_at        TEXT,
    cleaned_at        TEXT,
    summarized_at     TEXT,
    extra_json        TEXT NOT NULL DEFAULT '{}',
    title_norm        TEXT,
    has_fulltext      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_documents_doi        ON documents(doi);
CREATE INDEX IF NOT EXISTS idx_documents_title_norm ON documents(title_norm);
CREATE INDEX IF NOT EXISTS idx_documents_year       ON documents(year);
CREATE INDEX IF NOT EXISTS idx_documents_venue_type ON documents(venue_type);
CREATE INDEX IF NOT EXISTS idx_documents_fulltext   ON documents(has_fulltext);

CREATE TABLE IF NOT EXISTS document_tags (
    doc_id     TEXT NOT NULL,
    tag        TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0,
    source     TEXT NOT NULL DEFAULT 'rule-v1',
    PRIMARY KEY (doc_id, tag),
    FOREIGN KEY (doc_id) REFERENCES documents(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_document_tags_tag ON document_tags(tag);

CREATE TABLE IF NOT EXISTS chunks (
    id            TEXT PRIMARY KEY,
    doc_id        TEXT NOT NULL,
    ordinal       INTEGER NOT NULL DEFAULT 0,
    section       TEXT NOT NULL DEFAULT 'body',
    heading       TEXT,
    page_start    INTEGER,
    page_end      INTEGER,
    char_start    INTEGER,
    char_end      INTEGER,
    token_count   INTEGER NOT NULL DEFAULT 0,
    content_hash  TEXT
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc     ON chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_chunks_section ON chunks(section);

-- 正文单独一张表：检索时只取需要的片段，避免 documents 大表被反复扫
CREATE TABLE IF NOT EXISTS chunk_text (
    chunk_id        TEXT PRIMARY KEY,
    text            TEXT NOT NULL,
    normalized_text TEXT
);

-- 文献全文。放在初始 schema 中，保证空库首次打开网页也能正常查询。
CREATE TABLE IF NOT EXISTS documents_fulltext (
    doc_id TEXT PRIMARY KEY,
    text   TEXT NOT NULL
);

-- BM25 倒排索引
CREATE TABLE IF NOT EXISTS postings (
    term      TEXT NOT NULL,
    chunk_id  TEXT NOT NULL,
    tf        INTEGER NOT NULL,
    PRIMARY KEY (term, chunk_id)
);
CREATE INDEX IF NOT EXISTS idx_postings_chunk ON postings(chunk_id);

-- 精确 n-gram 指纹倒排（溯源核心）
CREATE TABLE IF NOT EXISTS fingerprints (
    fp        INTEGER NOT NULL,
    chunk_id  TEXT NOT NULL,
    position  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fingerprints_fp    ON fingerprints(fp);
CREATE INDEX IF NOT EXISTS idx_fingerprints_chunk ON fingerprints(chunk_id);

CREATE TABLE IF NOT EXISTS chunk_vectors (
    chunk_id     TEXT PRIMARY KEY,
    model        TEXT NOT NULL,
    dimension    INTEGER NOT NULL,
    vector       BLOB NOT NULL,
    content_hash TEXT,
    updated_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_chunk_vectors_model ON chunk_vectors(model);

CREATE TABLE IF NOT EXISTS summaries (
    doc_id         TEXT PRIMARY KEY,
    tldr           TEXT NOT NULL DEFAULT '',
    problem        TEXT NOT NULL DEFAULT '',
    methods        TEXT NOT NULL DEFAULT '',
    findings       TEXT NOT NULL DEFAULT '',
    contributions  TEXT NOT NULL DEFAULT '',
    limitations    TEXT NOT NULL DEFAULT '',
    keywords_json  TEXT NOT NULL DEFAULT '[]',
    claims_json    TEXT NOT NULL DEFAULT '[]',
    model          TEXT NOT NULL DEFAULT '',
    prompt_version TEXT NOT NULL DEFAULT '',
    content_hash   TEXT,
    created_at     TEXT
);

CREATE TABLE IF NOT EXISTS llm_cache (
    key         TEXT PRIMARY KEY,
    model       TEXT NOT NULL DEFAULT '',
    value       TEXT NOT NULL,
    created_at  TEXT
);

CREATE TABLE IF NOT EXISTS ingest_log (
    source      TEXT NOT NULL,
    query       TEXT NOT NULL,
    cursor      TEXT,
    status      TEXT NOT NULL DEFAULT 'pending',
    count       INTEGER NOT NULL DEFAULT 0,
    message     TEXT,
    updated_at  TEXT,
    PRIMARY KEY (source, query)
);

CREATE TABLE IF NOT EXISTS meta (
    key    TEXT PRIMARY KEY,
    value  TEXT
);

CREATE TABLE IF NOT EXISTS query_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT NOT NULL,
    query        TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    found        INTEGER NOT NULL DEFAULT 0,
    level        TEXT NOT NULL DEFAULT '',
    result_count INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_query_history_created ON query_history(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_query_history_kind ON query_history(kind, created_at DESC);
"""

#存储主类

#线程安全：写操作串行化
class Store:
    """SQLite 存储。线程安全（写操作用 RLock 串行化）。"""
#构造与连接登记

    def __init__(self,db_path:Path,*,read_only:bool=False)->None:
        self.db_path=Path(db_path)
        self.read_only=read_only  #只读实例跳过建目录和建表
        self._lock=threading.RLock()
        self._local=threading.local()
        self._conn:Optional[sqlite3.Connection]=None
        #普通列表加锁保护，不用弱引用集合
        self._all_connections:List[sqlite3.Connection]=[]
        self._conn_lock=threading.Lock()
#连接管理

    def connect(self)->sqlite3.Connection:
        """获取当前线程的连接（sqlite3 连接不能跨线程共用）。"""
        conn=getattr(self._local,"conn",None)  #拿不到就返回None
        if conn is not None:
            return conn

        if not self.read_only:  #只读模式下不建目录：那条路径上通常连创建权限都没有
            self.db_path.parent.mkdir(parents=True,exist_ok=True)
        if self.read_only:
            uri=self.db_path.resolve().as_uri()+"?mode=ro"
            conn=sqlite3.connect(
                uri,uri=True,timeout=30.0,check_same_thread=False,isolation_level=None,
            )
        else:
            conn=sqlite3.connect(
                str(self.db_path),
                timeout=30.0,  #30 秒超时：够扛过别的进程的长事务
                check_same_thread=False,
                isolation_level=None,
            )
        conn.row_factory=sqlite3.Row
        if not self.read_only:
            conn.execute("PRAGMA journal_mode=WAL")
        else:
            conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA cache_size=-64000")  #负数是 KB 计数
        conn.execute("PRAGMA foreign_keys=ON")
        self._local.conn=conn
        with self._conn_lock:
            self._all_connections.append(conn)  #登记进全局列表
        return conn

    def close(self)->None:  #只关当前线程的连接，刻意不越界去关别人的
        """关闭**当前线程**的连接。"""
        conn=getattr(self._local,"conn",None)
        if conn is not None:
            with self._conn_lock:
                if conn in self._all_connections:
                    self._all_connections.remove(conn)
            conn.close()
            self._local.conn=None  #置空线程局部，下次访问会重新建连

    def close_all(self)->int:
        """关闭本实例在所有线程里创建的连接，返回关闭数量。"""
        with self._conn_lock:
            connections=list(self._all_connections)
            self._all_connections.clear()
        closed=0
        for conn in connections:  #挨个关，单条失败不影响其余连接
            try:
                conn.close()
                closed+=1
            except Exception:
                pass
        self._local.conn=None  #最后把当前线程的引用也清掉
        return closed

    def __enter__(self)->"Store":
        self.connect()  #进入时先把连接建好
        return self

    def __exit__(self,*exc:Any)->None:
        self.close()  #只关当前线程的连接

    def init_schema(self)->None:
        """建表（幂等）。"""
        conn=self.connect()  #先拿当前线程的连接
        with self._lock:
            conn.executescript(SCHEMA_SQL)
            columns={
                row["name"] for row in conn.execute("PRAGMA table_info(chunk_text)")
            }
            if "normalized_text" not in columns:
                conn.execute("ALTER TABLE chunk_text ADD COLUMN normalized_text TEXT")

            # 旧库迁移：只补齐尚未规范化的片段，避免每次启动重复计算。
            missing=conn.execute(
                "SELECT chunk_id,text FROM chunk_text "
                "WHERE normalized_text IS NULL"
            ).fetchall()
            if missing:
                from litagent.index.phrase import exact_tokens

                conn.executemany(
                    "UPDATE chunk_text SET normalized_text=? WHERE chunk_id=?",
                    [
                        ("\x1f".join(exact_tokens(row["text"] or "")),row["chunk_id"])
                        for row in missing
                    ],
                )
            current=conn.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()
            if current is None or current["value"]!=SCHEMA_VERSION:
                conn.execute(
                    "INSERT INTO meta(key,value) VALUES('schema_version',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (SCHEMA_VERSION,),
                )
            self._backfill_document_tags(conn)

    @property  #exists写成属性而不是方法
    def exists(self)->bool:
        return self.db_path.is_file()

    def table_names(self)->List[str]:  #列出真实建出来的表名
        rows=self.connect().execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        return[r["name"] for r in rows]

    def set_meta(self,key:str,value:Any)->None:  #字符串原样存，其它类型一律json.dumps
        payload=value if isinstance(value,str) else json.dumps(value,ensure_ascii=False)
        with self._lock:
            self.connect().execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "  #冲突时改走更新分支，重复写同一个键不会报主键冲突
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key,payload),
            )

    @staticmethod
    def _document_tag_rows(doc:Document)->List[Tuple[str,str,float,str]]:
        """把文档标签与可解释分数转换成标签表行。"""
        from litagent.disciplines import (
            TAGGER_VERSION,
            classify_disciplines_with_scores,
        )

        scored=classify_disciplines_with_scores(
            title=doc.title or "",
            abstract=doc.abstract or "",
            keywords=doc.keywords,
            venue=doc.venue or "",
            fulltext=doc.fulltext or "",
        )
        score_by_tag={tag:score for tag,score in scored}
        tags=list(dict.fromkeys(doc.discipline_tags or list(score_by_tag)))
        return[
            (doc.id,tag,float(score_by_tag.get(tag,0.0)),TAGGER_VERSION)
            for tag in tags
            if tag
        ]

    def _write_document_tags(
        self,conn:sqlite3.Connection,doc:Document
    )->None:
        conn.execute("DELETE FROM document_tags WHERE doc_id=?",(doc.id,))
        rows=self._document_tag_rows(doc)
        if rows:
            conn.executemany(
                "INSERT INTO document_tags(doc_id,tag,confidence,source) "
                "VALUES(?,?,?,?)",
                rows,
            )

    def _backfill_document_tags(self,conn:sqlite3.Connection)->None:
        """为旧库文献补标签；分类规则升级时整体重建。"""
        from litagent.disciplines import TAGGER_VERSION

        row=conn.execute(
            "SELECT value FROM meta WHERE key='discipline_tags_version'"
        ).fetchone()
        if row is None or row["value"]!=TAGGER_VERSION:
            conn.execute("DELETE FROM document_tags")
        missing=conn.execute(
            "SELECT d.id,d.title,d.abstract,d.keywords_json,d.venue "
            "FROM documents d WHERE NOT EXISTS("
            " SELECT 1 FROM document_tags t WHERE t.doc_id=d.id)"
        ).fetchall()
        for item in missing:
            try:
                keywords=json.loads(item["keywords_json"] or "[]")
            except(ValueError,TypeError):
                keywords=[]
            doc=Document(
                id=item["id"],
                title=item["title"] or "",
                abstract=item["abstract"],
                keywords=keywords if isinstance(keywords,list) else[],
                venue=item["venue"],
            )
            self._write_document_tags(conn,doc)
        conn.execute(
            "INSERT INTO meta(key,value) VALUES('discipline_tags_version',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (TAGGER_VERSION,),
        )

    def rebuild_document_tags(self)->int:
        """清空并按当前规则重建所有文献标签，返回标签行数。"""
        conn=self.connect()
        with self._lock:
            conn.execute("DELETE FROM document_tags")
            self._backfill_document_tags(conn)
            return int(conn.execute(
                "SELECT COUNT(*) FROM document_tags"
            ).fetchone()[0])

    def get_document_tags(self,doc_id:str)->List[str]:
        return[
            row["tag"] for row in self.connect().execute(
                "SELECT tag FROM document_tags WHERE doc_id=? ORDER BY rowid",
                (doc_id,),
            )
        ]

    def get_document_tags_map(
        self,doc_ids:Sequence[str]
    )->Dict[str,List[str]]:
        result:Dict[str,List[str]]={doc_id:[] for doc_id in doc_ids}
        unique=list(dict.fromkeys(doc_ids))
        if not unique:
            return result
        conn=self.connect()
        for start in range(0,len(unique),400):
            batch=unique[start:start+400]
            marks=",".join(["?"]*len(batch))
            for row in conn.execute(
                "SELECT doc_id,tag FROM document_tags "
                "WHERE doc_id IN (%s) ORDER BY rowid"%marks,
                batch,
            ):
                result.setdefault(row["doc_id"],[]).append(row["tag"])
        return result

    def record_query_history(
        self,
        kind:str,
        query:str,
        *,
        found:bool=False,
        level:str="",
        result_count:int=0,
        metadata:Optional[Dict[str,Any]]=None,
    )->int:
        """保存一条检索、溯源或 Agent 查询历史。"""
        with self._lock:
            cursor=self.connect().execute(
                "INSERT INTO query_history("
                "kind,query,created_at,found,level,result_count,metadata_json"
                ") VALUES(?,?,?,?,?,?,?)",
                (
                    str(kind or "query"),
                    str(query or ""),
                    utcnow(),
                    1 if found else 0,
                    str(level or ""),
                    max(0,int(result_count)),
                    json.dumps(metadata or{},ensure_ascii=False),
                ),
            )
            return int(cursor.lastrowid)

    def list_query_history(
        self,*,limit:int=100,kind:Optional[str]=None
    )->List[Dict[str,Any]]:
        sql=(
            "SELECT id,kind,query,created_at,found,level,result_count,"
            "metadata_json FROM query_history"
        )
        params:List[Any]=[]
        if kind:
            sql+=" WHERE kind=?"
            params.append(kind)
        sql+=" ORDER BY created_at DESC,id DESC LIMIT ?"
        params.append(max(1,min(int(limit),1000)))
        rows=[]
        for row in self.connect().execute(sql,params):
            item=dict(row)
            try:
                item["metadata"]=json.loads(item.pop("metadata_json") or "{}")
            except(ValueError,TypeError):
                item["metadata"]={}
            item["found"]=bool(item["found"])
            rows.append(item)
        return rows

    def delete_query_history(self,history_id:int)->int:
        with self._lock:
            cursor=self.connect().execute(
                "DELETE FROM query_history WHERE id=?",(int(history_id),)
            )
            return int(cursor.rowcount)

    def clear_query_history(self,*,kind:Optional[str]=None)->int:
        with self._lock:
            if kind:
                cursor=self.connect().execute(
                    "DELETE FROM query_history WHERE kind=?",(kind,)
                )
            else:
                cursor=self.connect().execute("DELETE FROM query_history")
            return int(cursor.rowcount)

    def get_meta(self,key:str,default:Optional[str]=None)->Optional[str]:  #读取带默认值：键不存在时不用抛异常
        row=self.connect().execute(
            "SELECT value FROM meta WHERE key=?",(key,)
        ).fetchone()
        return row["value"] if row else default  #有行就取列值，没有就给默认值

    #文献表读写
    @staticmethod
    def _doc_to_row(doc:Document)->Tuple[Any,...]:
        content_level=str((doc.extra or {}).get("content_level") or "").strip()
        has_fulltext=bool(
            (doc.fulltext or "").strip()
            and content_level not in("title","abstract")
        )
        return(  #元组顺序就是插入语句里参数的顺序
            doc.id,
            doc.doi,
            doc.title or "",
            doc.title_en,
            json.dumps([a.to_dict() for a in doc.authors],ensure_ascii=False),  #作者对象逐个转字典再整体序列化，保留非 ASCII
            doc.year,
            doc.venue,
            doc.venue_type,
            doc.volume,
            doc.issue,
            doc.pages,
            doc.publisher,
            doc.publisher_place,
            doc.edition,
            doc.isbn,
            doc.url,
            doc.access_date,
            doc.language,
            doc.abstract,
            json.dumps(doc.keywords or[],ensure_ascii=False),  #关键词和下面的参考文献都兜底成空列表
            doc.source,
            doc.source_id,
            doc.license,
            1 if doc.is_oa else 0,  #布尔转 0/1：SQLite 没有布尔类型
            doc.pdf_path,
            doc.page_count,
            doc.citation_count,
            json.dumps(doc.references or[],ensure_ascii=False),  #参考文献存 JSON 文本，条目数不定
            doc.content_hash,
            doc.fetched_at,
            doc.cleaned_at,
            doc.summarized_at,
            json.dumps(doc.extra or{},ensure_ascii=False),  #附加字段同样走 JSON
            normalize_title(doc.title),
            1 if has_fulltext else 0,
        )
#列清单：和元组逐位对应

    _DOC_COLUMNS=(
        "id, doi, title, title_en, authors_json, year, venue, venue_type, volume, issue, "
        "pages, publisher, publisher_place, edition, isbn, url, access_date, language, "  #每行只放五六个列名
        "abstract, keywords_json, source, source_id, license, is_oa, pdf_path, page_count, "
        "citation_count, references_json, content_hash, fetched_at, cleaned_at, "
        "summarized_at, extra_json, title_norm, has_fulltext"
    )
#全文标记只升不降

    #照这个None重算就会把有全文的记录标成没全文
    #对比一下：不这么写
    _FULLTEXT_MONOTONIC=(
        "has_fulltext = CASE WHEN excluded.has_fulltext = 1 "  #CASE WHEN把两边的值放在一起比较
        "THEN 1 ELSE documents.has_fulltext END"
    )

    def upsert_document(self,doc:Document)->None:  #写入或更新一篇文献，调用方用它即可
        """插入或更新一篇文献。"""
        placeholders=", ".join(["?"]*35)
        sql=(
            "INSERT INTO documents (%s) VALUES (%s) "
            "ON CONFLICT(id) DO UPDATE SET "
            "doi=excluded.doi, title=excluded.title, title_en=excluded.title_en, "
            "authors_json=excluded.authors_json, year=excluded.year, venue=excluded.venue, "
            "venue_type=excluded.venue_type, volume=excluded.volume, issue=excluded.issue, "
            "pages=excluded.pages, publisher=excluded.publisher, "
            "publisher_place=excluded.publisher_place, edition=excluded.edition, "
            "isbn=excluded.isbn, url=excluded.url, access_date=excluded.access_date, "
            "language=excluded.language, abstract=COALESCE(excluded.abstract, documents.abstract), "  #摘要这一列例外：新值有内容才覆盖，否则保留旧值
            "keywords_json=excluded.keywords_json, source=excluded.source, "
            "source_id=excluded.source_id, license=excluded.license, is_oa=excluded.is_oa, "
            "pdf_path=excluded.pdf_path, page_count=excluded.page_count, "
            "citation_count=excluded.citation_count, references_json=excluded.references_json, "
            "content_hash=excluded.content_hash, fetched_at=excluded.fetched_at, "
            "cleaned_at=excluded.cleaned_at, summarized_at=excluded.summarized_at, "
            "extra_json=excluded.extra_json, title_norm=excluded.title_norm, "
            +self._FULLTEXT_MONOTONIC
        )%(self._DOC_COLUMNS,placeholders)
        with self._lock:
            conn=self.connect()
            conn.execute(sql,self._doc_to_row(doc))
            self._write_document_tags(conn,doc)

    def upsert_documents(self,docs:Iterable[Document])->int:
        """批量写入，返回写入条数。明显快于逐条调用。"""
        count=0
        placeholders=", ".join(["?"]*35)
        sql=(
            "INSERT INTO documents (%s) VALUES (%s) "
            "ON CONFLICT(id) DO UPDATE SET "
            "doi=excluded.doi, title=excluded.title, title_en=excluded.title_en, "
            "authors_json=excluded.authors_json, year=excluded.year, venue=excluded.venue, "
            "venue_type=excluded.venue_type, volume=excluded.volume, issue=excluded.issue, "
            "pages=excluded.pages, publisher=excluded.publisher, "
            "publisher_place=excluded.publisher_place, edition=excluded.edition, "
            "isbn=excluded.isbn, url=excluded.url, access_date=excluded.access_date, "
            "language=excluded.language, abstract=COALESCE(excluded.abstract, documents.abstract), "
            "keywords_json=excluded.keywords_json, source=excluded.source, "
            "source_id=excluded.source_id, license=excluded.license, is_oa=excluded.is_oa, "
            "pdf_path=excluded.pdf_path, page_count=excluded.page_count, "
            "citation_count=excluded.citation_count, references_json=excluded.references_json, "
            "content_hash=excluded.content_hash, fetched_at=excluded.fetched_at, "
            "cleaned_at=excluded.cleaned_at, summarized_at=excluded.summarized_at, "
            "extra_json=excluded.extra_json, title_norm=excluded.title_norm, "
            +self._FULLTEXT_MONOTONIC
        )%(self._DOC_COLUMNS,placeholders)
        conn=self.connect()
        with self._lock:
            conn.execute("BEGIN")
            try:
                for doc in docs:
                    conn.execute(sql,self._doc_to_row(doc))
                    self._write_document_tags(conn,doc)
                    count+=1
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return count

    @staticmethod
    def _row_to_doc(row:sqlite3.Row,fulltext:Optional[str]=None)->Document:
        return Document(
            id=row["id"],
            doi=row["doi"],
            title=row["title"] or "",
            title_en=row["title_en"],
            authors=[Author.from_dict(a) for a in json.loads(row["authors_json"] or "[]")],
            year=row["year"],
            venue=row["venue"],
            venue_type=row["venue_type"] or "unknown",
            volume=row["volume"],
            issue=row["issue"],
            pages=row["pages"],
            publisher=row["publisher"],
            publisher_place=row["publisher_place"],
            edition=row["edition"],
            isbn=row["isbn"],
            url=row["url"],
            access_date=row["access_date"],
            language=row["language"] or "en",
            abstract=row["abstract"],
            fulltext=fulltext,
            keywords=json.loads(row["keywords_json"] or "[]"),
            source=row["source"] or "unknown",
            source_id=row["source_id"],
            license=row["license"],
            is_oa=bool(row["is_oa"]),
            pdf_path=row["pdf_path"],
            page_count=row["page_count"],
            citation_count=row["citation_count"],
            references=json.loads(row["references_json"] or "[]"),
            content_hash=row["content_hash"],
            fetched_at=row["fetched_at"],
            cleaned_at=row["cleaned_at"],
            summarized_at=row["summarized_at"],
            extra=json.loads(row["extra_json"] or "{}"),
        )

    def get_document(self,doc_id:str,*,with_fulltext:bool=True)->Optional[Document]:
        """按 ID 取文献。默认带全文；列表场景传 with_fulltext=False 提速。"""
        row=self.connect().execute(
            "SELECT %s FROM documents WHERE id=?"%self._DOC_COLUMNS,(doc_id,)
        ).fetchone()
        if row is None:
            return None
        fulltext=None
        if with_fulltext:
            fulltext=self.get_fulltext(doc_id)
        return self._row_to_doc(row,fulltext)

    def get_documents(self,doc_ids:Sequence[str],*,with_fulltext:bool=False
                      )->Dict[str,Document]:
        """批量按 ID 取文献，返回 {doc_id: Document}。"""
        if not doc_ids:
            return{}
        result:Dict[str,Document]={}
        conn=self.connect()
        unique=list(dict.fromkeys(doc_ids))
        chunk_size=400  #避开 SQLite 变量数上限
        for start in range(0,len(unique),chunk_size):
            batch=unique[start:start+chunk_size]
            marks=",".join(["?"]*len(batch))
            rows=conn.execute(
                "SELECT %s FROM documents WHERE id IN (%s)"%(self._DOC_COLUMNS,marks),
                batch,
            ).fetchall()
            for row in rows:
                fulltext=self.get_fulltext(row["id"]) if with_fulltext else None
                result[row["id"]]=self._row_to_doc(row,fulltext)
        return result

    def find_by_doi(self,doi:str)->Optional[Document]:
        norm=normalize_doi(doi)
        if not norm:
            return None
        row=self.connect().execute(
            "SELECT %s FROM documents WHERE doi=? LIMIT 1"%self._DOC_COLUMNS,(norm,)
        ).fetchone()
        if row is None:
            #历史数据里 doi 可能没归一化
            row=self.connect().execute(
                "SELECT %s FROM documents WHERE LOWER(doi) LIKE ? LIMIT 1"%self._DOC_COLUMNS,
                ("%"+norm+"%",),
            ).fetchone()
        return self._row_to_doc(row) if row else None

    def find_by_title(self,title:str,*,threshold:float=0.92,
                      limit:int=200)->Optional[Document]:
        """按标题查重：先精确匹配归一化标题，再对候选做 3-gram 相似度。"""
        norm=normalize_title(title)
        if not norm:
            return None
        conn=self.connect()
        row=conn.execute(
            "SELECT %s FROM documents WHERE title_norm=? LIMIT 1"%self._DOC_COLUMNS,(norm,)
        ).fetchone()
        if row is not None:
            return self._row_to_doc(row)

        #用标题前 8 个字符做粗筛，缩小比对范围
        prefix=norm[:8]
        rows=conn.execute(
            "SELECT %s FROM documents WHERE title_norm LIKE ? LIMIT ?"%self._DOC_COLUMNS,
            (prefix+"%",limit),
        ).fetchall()
        best:Optional[Document]=None
        best_score=0.0
        for candidate in rows:
            score=title_similarity(title,candidate["title"])
            if score>best_score:
                best_score,best=score,candidate
        if best is not None and best_score>=threshold:
            return self._row_to_doc(best)
        return None

    def iter_documents(self,*,with_fulltext:bool=False,
                       only_cleaned:bool=False,limit:Optional[int]=None
                       )->Iterator[Document]:
        """遍历文献。only_cleaned=True 只返回已清洗（有全文）的。"""
        sql="SELECT %s FROM documents"%self._DOC_COLUMNS
        if only_cleaned:
            sql+=" WHERE has_fulltext=1"
        sql+=" ORDER BY year DESC, id"
        if limit:
            sql+=" LIMIT %d"%int(limit)
        for row in self.connect().execute(sql):
            fulltext=self.get_fulltext(row["id"]) if with_fulltext else None
            yield self._row_to_doc(row,fulltext)

    def count_documents(self,*,only_with_fulltext:bool=False)->int:
        sql="SELECT COUNT(*) AS n FROM documents"
        if only_with_fulltext:
            sql+=" WHERE has_fulltext=1"
        return int(self.connect().execute(sql).fetchone()["n"])

    def set_fulltext(self,doc_id:str,text:str,*,mark_fulltext:bool=True)->None:
        """写入正文；摘要/标题兜底内容可以存，但不计为“全文”。"""
        conn=self.connect()
        with self._lock:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS documents_fulltext ("
                "doc_id TEXT PRIMARY KEY, text TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT INTO documents_fulltext(doc_id, text) VALUES(?, ?) "
                "ON CONFLICT(doc_id) DO UPDATE SET text=excluded.text",
                (doc_id,text or ""),
            )
            conn.execute(
                "UPDATE documents SET has_fulltext=? WHERE id=?",
                (1 if(mark_fulltext and (text or "").strip()) else 0,doc_id),
            )

    def get_fulltext(self,doc_id:str)->Optional[str]:
        try:
            row=self.connect().execute(
                "SELECT text FROM documents_fulltext WHERE doc_id=?",(doc_id,)
            ).fetchone()
        except sqlite3.OperationalError:
            return None
        return row["text"] if row else None

    def has_fulltext(self,doc_id:str)->bool:
        """该文献是否真的有全文（摘要/标题兜底不算）。"""
        try:
            row=self.connect().execute(
                "SELECT 1 FROM documents WHERE id=? AND has_fulltext=1 LIMIT 1",
                (doc_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            return False
        return row is not None

    def repair_fulltext_flags(self)->int:
        """按 documents_fulltext 表重算 has_fulltext，返回修正的行数。"""
        conn=self.connect()
        #先把确实有正文的补上；有 chunks 且不是标题/摘要兜底，才算全文
        cursor=conn.execute(
            "UPDATE documents SET has_fulltext = 1 WHERE has_fulltext = 0 AND EXISTS ("
            "  SELECT 1 FROM documents_fulltext f "
            "  WHERE f.doc_id = documents.id AND LENGTH(TRIM(f.text)) > 0) "
            "AND COALESCE(json_extract(extra_json, '$.content_level'), '') "
            "NOT IN ('title','abstract') "
            "AND EXISTS (SELECT 1 FROM chunks c WHERE c.doc_id = documents.id "
            "AND c.section NOT IN ('title','abstract'))"
        )
        fixed=cursor.rowcount or 0
        #再把正文被清空、标记却还是 1 的降下来
        cursor=conn.execute(
            "UPDATE documents SET has_fulltext = 0 WHERE has_fulltext = 1 AND NOT EXISTS ("
            "  SELECT 1 FROM documents_fulltext f "
            "  WHERE f.doc_id = documents.id AND LENGTH(TRIM(f.text)) > 0)"
        )
        fixed+=cursor.rowcount or 0
        #摘要/标题兜底虽然会存进 documents_fulltext 供检索，但不能冒充全文。
        cursor=conn.execute(
            "UPDATE documents SET has_fulltext = 0 WHERE has_fulltext = 1 AND ("
            "json_extract(extra_json, '$.content_level') IN ('title','abstract') "
            "OR NOT EXISTS (SELECT 1 FROM chunks c WHERE c.doc_id = documents.id "
            "AND c.section NOT IN ('title','abstract')))"
        )
        fixed+=cursor.rowcount or 0
        return fixed

    def delete_document(self,doc_id:str)->None:
        """彻底删除一篇文献及其全部派生数据；整批删除在一个事务里完成。"""
        conn=self.connect()
        with self._lock:
            conn.execute("BEGIN")
            try:
                conn.execute("DELETE FROM postings WHERE chunk_id IN "
                             "(SELECT id FROM chunks WHERE doc_id=?)",(doc_id,))
                conn.execute("DELETE FROM fingerprints WHERE chunk_id IN "
                             "(SELECT id FROM chunks WHERE doc_id=?)",(doc_id,))
                conn.execute("DELETE FROM chunk_vectors WHERE chunk_id IN "
                             "(SELECT id FROM chunks WHERE doc_id=?)",(doc_id,))
                conn.execute("DELETE FROM chunk_text WHERE chunk_id IN "
                             "(SELECT id FROM chunks WHERE doc_id=?)",(doc_id,))
                conn.execute("DELETE FROM chunks WHERE doc_id=?",(doc_id,))
                conn.execute("DELETE FROM summaries WHERE doc_id=?",(doc_id,))
                try:
                    conn.execute("DELETE FROM documents_fulltext WHERE doc_id=?",(doc_id,))
                except sqlite3.OperationalError:
                    pass
                conn.execute("DELETE FROM documents WHERE id=?",(doc_id,))
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    #chunks
    def replace_chunks(self,doc_id:str,chunks:Sequence[Chunk],
                       *,term_freqs:Optional[Dict[str,Dict[str,int]]]=None,
                       fingerprints:Optional[Dict[str,List[int]]]=None)->int:
        """原子性地替换一篇文献的全部片段及派生索引。"""
        from litagent.index.phrase import exact_tokens

        conn=self.connect()
        with self._lock:
            conn.execute("BEGIN")
            try:
                conn.execute("DELETE FROM postings WHERE chunk_id IN "
                             "(SELECT id FROM chunks WHERE doc_id=?)",(doc_id,))
                conn.execute("DELETE FROM fingerprints WHERE chunk_id IN "
                             "(SELECT id FROM chunks WHERE doc_id=?)",(doc_id,))
                conn.execute("DELETE FROM chunk_vectors WHERE chunk_id IN "
                             "(SELECT id FROM chunks WHERE doc_id=?)",(doc_id,))
                conn.execute("DELETE FROM chunk_text WHERE chunk_id IN "
                             "(SELECT id FROM chunks WHERE doc_id=?)",(doc_id,))
                conn.execute("DELETE FROM chunks WHERE doc_id=?",(doc_id,))

                conn.executemany(
                    "INSERT INTO chunks (id, doc_id, ordinal, section, heading, page_start, "
                    "page_end, char_start, char_end, token_count, content_hash) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    [
                        (c.id,c.doc_id,c.ordinal,c.section,c.heading,c.page_start,
                         c.page_end,c.char_start,c.char_end,c.token_count,c.content_hash)
                        for c in chunks
                    ],
                )
                conn.executemany(
                    "INSERT INTO chunk_text (chunk_id, text, normalized_text) "
                    "VALUES (?,?,?)",
                    [
                        (
                            c.id,
                            c.text,
                            "\x1f".join(exact_tokens(c.text)),
                        )
                        for c in chunks
                    ],
                )
                if term_freqs:
                    conn.executemany(
                        "INSERT OR REPLACE INTO postings (term, chunk_id, tf) VALUES (?,?,?)",
                        [
                            (term,chunk_id,tf)
                            for chunk_id,freqs in term_freqs.items()
                            for term,tf in freqs.items()
                        ],
                    )
                if fingerprints:
                    conn.executemany(
                        "INSERT INTO fingerprints (fp, chunk_id, position) VALUES (?,?,?)",
                        [
                            (fp,chunk_id,pos)
                            for chunk_id,fps in fingerprints.items()
                            for pos,fp in enumerate(fps)
                        ],
                    )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return len(chunks)

    def replace_chunk_vectors(
        self,doc_id:str,vectors:Dict[str,bytes],*,
        model:str,dimension:int,
    )->int:
        """原子替换一篇文献的片段向量。"""
        conn=self.connect()
        with self._lock:
            conn.execute("BEGIN")
            try:
                conn.execute("DELETE FROM chunk_vectors WHERE chunk_id IN "
                             "(SELECT id FROM chunks WHERE doc_id=?)",(doc_id,))
                rows=conn.execute(
                    "SELECT id,content_hash FROM chunks WHERE doc_id=?",
                    (doc_id,),
                ).fetchall()
                now=utcnow()
                conn.executemany(
                    "INSERT INTO chunk_vectors("
                    "chunk_id,model,dimension,vector,content_hash,updated_at"
                    ") VALUES(?,?,?,?,?,?)",
                    [
                        (
                            row["id"],model,int(dimension),
                            vectors[row["id"]],row["content_hash"],now,
                        )
                        for row in rows
                        if row["id"] in vectors
                    ],
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return len(vectors)

    def get_chunk_text(self,chunk_id:str)->Optional[str]:
        row=self.connect().execute(
            "SELECT text FROM chunk_text WHERE chunk_id=?",(chunk_id,)
        ).fetchone()
        return row["text"] if row else None

    def get_chunk(self,chunk_id:str)->Optional[Chunk]:
        row=self.connect().execute(
            "SELECT id, doc_id, ordinal, section, heading, page_start, page_end, "
            "char_start, char_end, token_count, content_hash FROM chunks WHERE id=?",
            (chunk_id,),
        ).fetchone()
        if row is None:
            return None
        chunk=Chunk.from_dict(dict(row))
        chunk.text=self.get_chunk_text(chunk_id) or ""
        return chunk

    def iter_chunks(self,doc_id:Optional[str]=None)->Iterator[Chunk]:
        conn=self.connect()
        if doc_id:
            rows=conn.execute(
                "SELECT id, doc_id, ordinal, section, heading, page_start, page_end, "
                "char_start, char_end, token_count, content_hash FROM chunks "
                "WHERE doc_id=? ORDER BY ordinal",(doc_id,),
            )
        else:
            rows=conn.execute(
                "SELECT id, doc_id, ordinal, section, heading, page_start, page_end, "
                "char_start, char_end, token_count, content_hash FROM chunks ORDER BY doc_id, ordinal"
            )
        for row in rows:
            chunk=Chunk.from_dict(dict(row))
            chunk.text=self.get_chunk_text(chunk.id) or ""
            yield chunk

    def get_chunk_texts(self,chunk_ids:Sequence[str])->Dict[str,str]:
        """批量取片段正文，返回 {chunk_id: text}。"""
        if not chunk_ids:
            return{}
        conn=self.connect()
        result:Dict[str,str]={}
        unique=list(dict.fromkeys(chunk_ids))
        for start in range(0,len(unique),400):
            batch=unique[start:start+400]
            marks=",".join(["?"]*len(batch))
            for row in conn.execute(
                "SELECT chunk_id, text FROM chunk_text WHERE chunk_id IN (%s)"%marks,batch
            ):
                result[row["chunk_id"]]=row["text"]
        return result

    def direct_normalized_matches(self,normalized_text:str)->List[Tuple[str,str]]:
        """用规范化词元串做短查询直接定位，避免 JSON/空白形态导致的慢回退。"""
        if not normalized_text:
            return[]
        try:
            rows=self.connect().execute(
                "SELECT chunk_id,text FROM chunk_text "
                "WHERE instr(COALESCE(normalized_text,''),?)>0",
                (normalized_text,),
            ).fetchall()
        except sqlite3.OperationalError:
            return[]
        return[(row["chunk_id"],row["text"]) for row in rows]

    def count_chunks(self)->int:
        return int(self.connect().execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"])

    def avg_chunk_length(self)->float:
        """BM25 的 avgdl。"""
        row=self.connect().execute(
            "SELECT AVG(token_count) AS a FROM chunks"
        ).fetchone()
        return float(row["a"]) if row and row["a"] else 1.0

    #postings（BM25 倒排索引）
    def postings_for_terms(self,terms:Sequence[str],*,max_df_ratio:float=1.0
                           )->Dict[str,List[Tuple[str,int]]]:
        """取若干词条的倒排链，返回 {term: [(chunk_id, tf), ...]}"""
        #空词表早退
        if not terms:
            return{}
        conn=self.connect()
        total=self.count_chunks() or 1
        max_df=max(1,int(total*max_df_ratio))
        result:Dict[str,List[Tuple[str,int]]]={}
        unique=list(dict.fromkeys(terms))
        for start in range(0,len(unique),300):
            batch=unique[start:start+300]  #切片取本批，切片越界不报错
            marks=",".join(["?"]*len(batch))
            for row in conn.execute(
                "SELECT term, chunk_id, tf FROM postings WHERE term IN (%s)"%marks,batch
            ):
                result.setdefault(row["term"],[]).append((row["chunk_id"],row["tf"]))  #用setdefault按词条归链
        return{t:chain[:max_df] if len(chain)>max_df else chain
                for t,chain in result.items()}

    #词条文档频率
    def term_document_frequency(self,terms:Sequence[str])->Dict[str,int]:
        """词条的文档频率 df，BM25 的 IDF 需要。"""
        if not terms:
            return{}
        conn=self.connect()  #提前取连接
        result:Dict[str,int]={}
        unique=list(dict.fromkeys(terms))
        for start in range(0,len(unique),300):
            batch=unique[start:start+300]  #切片切出本批词条
            marks=",".join(["?"]*len(batch))
            for row in conn.execute(
                "SELECT term, COUNT(*) AS df FROM postings WHERE term IN (%s) GROUP BY term"
                %marks,batch
            ):
                result[row["term"]]=int(row["df"])  #COUNT(*)出来是整数型，显式to int收口
        return result

    def count_postings(self)->int:  #整表计数，不带任何条件
        return int(self.connect().execute("SELECT COUNT(*) AS n FROM postings").fetchone()["n"])

    def vocabulary_size(self)->int:  #DISTINCT数的是不同词条的个数，不是行数
        row=self.connect().execute(
            "SELECT COUNT(DISTINCT term) AS n FROM postings"
        ).fetchone()
        return int(row["n"]) if row else 0  #查不到行时兜底成0

    #指纹倒排查询
    def fingerprint_lookup(self,fps:Sequence[int])->Dict[int,List[Tuple[str,int]]]:
        """按指纹批量查倒排链，返回 {fp: [(chunk_id, position), ...]}。"""
        if not fps:
            return{}
        conn=self.connect()
        result:Dict[int,List[Tuple[str,int]]]={}  #解码出来的fp类型可能不是int
        unique=list(dict.fromkeys(int(f) for f in fps))
        for start in range(0,len(unique),500):
            batch=unique[start:start+500]  #切出本批指纹
            marks=",".join(["?"]*len(batch))
            for row in conn.execute(
                "SELECT fp, chunk_id, position FROM fingerprints WHERE fp IN (%s)"%marks,
                batch,
            ):
                result.setdefault(int(row["fp"]),[]).append(  #查出来的fp再转一次int
                    (row["chunk_id"],int(row["position"]))
                )
        return result

    #片段指纹序列
    def chunk_fingerprints(self,chunk_id:str)->List[int]:
        rows=self.connect().execute(
            "SELECT fp FROM fingerprints WHERE chunk_id=? ORDER BY position",(chunk_id,)
        ).fetchall()
        return[int(r["fp"]) for r in rows]

    def count_fingerprints(self)->int:
        return int(
            self.connect().execute("SELECT COUNT(*) AS n FROM fingerprints").fetchone()["n"]  #整表计数，用于统计面板展示
        )

    #文献建表探测
    def doc_has_postings(self,doc_id:str)->bool:
        """该文献的片段是否已建好 BM25 倒排表。"""
        row=self.connect().execute(
            "SELECT 1 FROM postings WHERE chunk_id IN "  #子查询先按doc_id圈出该文献的全部片段
            "(SELECT id FROM chunks WHERE doc_id=?) LIMIT 1",
            (doc_id,),
        ).fetchone()
        return row is not None  #取到行说明有倒排记录，取不到说明还没建

    def doc_has_fingerprints(self,doc_id:str)->bool:
        """该文献的片段是否已建好精确指纹。"""
        row=self.connect().execute(  #和倒排表的探测写法一致，只换了一张表
            "SELECT 1 FROM fingerprints WHERE chunk_id IN "
            "(SELECT id FROM chunks WHERE doc_id=?) LIMIT 1",
            (doc_id,),
        ).fetchone()
        return row is not None  #回布尔值而不是计数，调用方只关心建没建过

    def docs_needing_lexical_index(self,*,fingerprint_n:Optional[int]=None)->List[str]:
        """返回缺 BM25 倒排或缺精确指纹的文献 ID，用于增量建索引。"""
        n=fingerprint_n if fingerprint_n and fingerprint_n>=3 else int(self.get_meta("fingerprint_n") or 7)
        rows=self.connect().execute(
            "SELECT DISTINCT c.doc_id AS doc_id FROM chunks c "
            "WHERE NOT EXISTS (SELECT 1 FROM postings p WHERE p.chunk_id = c.id) "
            "OR (c.token_count>=? AND NOT EXISTS ("
            "SELECT 1 FROM fingerprints f WHERE f.chunk_id = c.id)) "
            "ORDER BY c.doc_id",
            (n,),
        ).fetchall()
        return[row["doc_id"] for row in rows]

    def docs_needing_vector_index(
        self,*,model:str,dimension:int
    )->List[str]:
        rows=self.connect().execute(
            "SELECT DISTINCT c.doc_id AS doc_id FROM chunks c "
            "WHERE NOT EXISTS (SELECT 1 FROM chunk_vectors v "
            "WHERE v.chunk_id=c.id AND v.model=? AND v.dimension=? "
            "AND v.content_hash IS c.content_hash) "
            "ORDER BY c.doc_id",
            (model,int(dimension)),
        ).fetchall()
        return[row["doc_id"] for row in rows]

    def count_chunk_vectors(self,*,model:Optional[str]=None)->int:
        if model:
            row=self.connect().execute(
                "SELECT COUNT(*) AS n FROM chunk_vectors WHERE model=?",
                (model,),
            ).fetchone()
        else:
            row=self.connect().execute(
                "SELECT COUNT(*) AS n FROM chunk_vectors"
            ).fetchone()
        return int(row["n"])

    def clear_chunk_vectors(self,*,model:Optional[str]=None)->int:
        with self._lock:
            if model:
                cursor=self.connect().execute(
                    "DELETE FROM chunk_vectors WHERE model=?",(model,)
                )
            else:
                cursor=self.connect().execute("DELETE FROM chunk_vectors")
            return int(cursor.rowcount)

    #summaries
    def upsert_summary(self,summary:Summary)->None:
        if not summary.created_at:
            summary.created_at=utcnow()
        with self._lock:
            self.connect().execute(
                "INSERT INTO summaries (doc_id, tldr, problem, methods, findings, "
                "contributions, limitations, keywords_json, claims_json, model, "  #十三个字段全列出来不给省略，表结构改了这里立刻报错
                "prompt_version, content_hash, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(doc_id) DO UPDATE SET tldr=excluded.tldr, problem=excluded.problem, "
                "methods=excluded.methods, findings=excluded.findings, "
                "contributions=excluded.contributions, limitations=excluded.limitations, "
                "keywords_json=excluded.keywords_json, claims_json=excluded.claims_json, "
                "model=excluded.model, prompt_version=excluded.prompt_version, "
                "content_hash=excluded.content_hash, created_at=excluded.created_at",  #created_at也跟着覆盖
                (
                    summary.doc_id,summary.tldr,summary.problem,summary.methods,
                    summary.findings,summary.contributions,summary.limitations,
                    json.dumps(summary.keywords,ensure_ascii=False),
                    json.dumps([c.to_dict() for c in summary.claims],ensure_ascii=False),
                    summary.model,summary.prompt_version,summary.content_hash,
                    summary.created_at,
                ),
            )

    #行转总结对象
    @staticmethod
    def _row_to_summary(row:sqlite3.Row)->Summary:
        return Summary(
            doc_id=row["doc_id"],
            tldr=row["tldr"] or "",  #文本字段逐个补空串兜底：老库里可能是NULL
            problem=row["problem"] or "",
            methods=row["methods"] or "",
            findings=row["findings"] or "",
            contributions=row["contributions"] or "",
            limitations=row["limitations"] or "",
            keywords=json.loads(row["keywords_json"] or "[]"),  #关键词是JSON存的；老行或空值兜底成[]
            claims=[Claim.from_dict(c) for c in json.loads(row["claims_json"] or "[]")],
            model=row["model"] or "",
            prompt_version=row["prompt_version"] or "",
            content_hash=row["content_hash"] or "",
            created_at=row["created_at"] or "",
        )

    #取单篇总结
    def get_summary(self,doc_id:str)->Optional[Summary]:
        row=self.connect().execute(
            "SELECT * FROM summaries WHERE doc_id=?",(doc_id,)
        ).fetchone()
        return self._row_to_summary(row) if row else None  #查不到就回None而不是抛异常

    def count_summaries(self)->int:
        return int(self.connect().execute("SELECT COUNT(*) AS n FROM summaries").fetchone()["n"])  #整表计数，给统计面板用

    def docs_needing_summary(self,prompt_version:str,*,only_with_fulltext:bool=True,
                             limit:Optional[int]=None)->List[str]:
        """返回还没有（当前 prompt 版本的）总结的文献 ID，支撑断点续跑"""
        sql=(  #SQL逐步加条件，所以先拼基础部分
            "SELECT d.id AS id FROM documents d "
            "LEFT JOIN summaries s ON s.doc_id = d.id AND s.prompt_version = ? "
            "WHERE s.doc_id IS NULL"
        )
        if only_with_fulltext:  #默认只挑有全文的：没正文的文献总结不出来
            sql+=" AND d.has_fulltext=1"
        sql+=" ORDER BY d.year DESC"
        if limit:
            sql+=" LIMIT %d"%int(limit)  #数值先to int再拼进SQL
        return[r["id"] for r in self.connect().execute(sql,(prompt_version,))]

    #llm cache
    def cache_get(self,key:str,model:str="")->Optional[str]:
        """取 LLM 结果缓存，未命中返回 None 而不是抛异常"""
        row=self.connect().execute(
            "SELECT value, model FROM llm_cache WHERE key=?",(key,)  #key是主键，最多命中一行
        ).fetchone()
        if row is None:
            return None
        if model and row["model"] and row["model"]!=model:  #三条件缺一不可
            return None
        return row["value"]

    #缓存写入
    def cache_put(self,key:str,value:str,model:str="")->None:
        with self._lock:
            self.connect().execute(
                "INSERT INTO llm_cache (key, model, value, created_at) VALUES (?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET model=excluded.model, value=excluded.value, "  #冲突按key覆盖
                "created_at=excluded.created_at",
                (key,model,value,utcnow()),
            )

    #缓存计数
    def cache_count(self)->int:
        return int(self.connect().execute("SELECT COUNT(*) AS n FROM llm_cache").fetchone()["n"])

    #ingest log（断点续传）
    def mark_ingest(self,source:str,query:str,*,cursor:Optional[str]=None,
                    status:str="done",count:int=0,
                    message:Optional[str]=None)->None:
        with self._lock:
            self.connect().execute(
                "INSERT INTO ingest_log (source, query, cursor, status, count, message, updated_at) "
                "VALUES (?,?,?,?,?,?,?) ON CONFLICT(source, query) DO UPDATE SET "  #冲突键是来源加检索式的组合
                "cursor=excluded.cursor, status=excluded.status, count=excluded.count, "
                "message=excluded.message, updated_at=excluded.updated_at",
                (source,query,cursor,status,count,message,utcnow()),
            )  #时间戳现取现写

    def get_ingest(self,source:str,query:str)->Optional[Dict[str,Any]]:
        row=self.connect().execute(
            "SELECT * FROM ingest_log WHERE source=? AND query=?",(source,query)  #source和query一起做条件
        ).fetchone()
        return dict(row) if row else None

    #进度列表
    def list_ingest(self,source:Optional[str]=None)->List[Dict[str,Any]]:
        conn=self.connect()
        if source:
            rows=conn.execute(
                "SELECT * FROM ingest_log WHERE source=? ORDER BY updated_at DESC",(source,)
            )
        else:
            rows=conn.execute("SELECT * FROM ingest_log ORDER BY updated_at DESC")  #不加条件的全量列表
        return[dict(r) for r in rows]

    #整理数据库
    def vacuum(self)->None:
        with self._lock:
            self.connect().execute("VACUUM")

    #重建统计与索引
    def optimize(self)->None:
        """重建统计信息并整理索引，批量写入后调用一次即可"""
        conn=self.connect()
        with self._lock:
            conn.execute("ANALYZE")  #ANALYZE重建统计信息
            conn.execute("PRAGMA optimize")

    def stats(self)->Dict[str,Any]:
        """汇总全库统计，供 cli.py stats 与界面展示"""
        conn=self.connect()
        result:Dict[str,Any]={
            "db_path":str(self.db_path),
            "db_size_bytes":self.db_path.stat().st_size if self.db_path.is_file() else 0,  #文件不在就回0
            "documents":0,"documents_with_fulltext":0,"chunks":0,
            "tokens_total":0,"postings":0,"vocabulary":0,
            "fingerprints":0,"vectors":0,"summaries":0,"llm_cache":0,
            "by_venue_type":{},"by_source":{},"by_year":{},"by_language":{},  #四个分组统计预置成空字典
        }
        try:
            result["documents"]=self.count_documents()
            result["documents_with_fulltext"]=self.count_documents(only_with_fulltext=True)  #只看有全文的那些，反映真正能进检索的规模
            result["chunks"]=self.count_chunks()
            row=conn.execute("SELECT SUM(token_count) AS s FROM chunks").fetchone()
            result["tokens_total"]=int(row["s"] or 0)
            result["postings"]=self.count_postings()
            result["vocabulary"]=self.vocabulary_size()
            result["fingerprints"]=self.count_fingerprints()
            result["vectors"]=self.count_chunk_vectors()
            result["summaries"]=self.count_summaries()
            result["llm_cache"]=self.cache_count()
            result["by_venue_type"]={
                r["venue_type"]:r["n"] for r in conn.execute(
                    "SELECT venue_type, COUNT(*) AS n FROM documents GROUP BY venue_type "
                    "ORDER BY n DESC")
            }
            result["by_source"]={
                r["source"]:r["n"] for r in conn.execute(
                    "SELECT source, COUNT(*) AS n FROM documents GROUP BY source ORDER BY n DESC")
            }
            result["by_language"]={
                r["language"]:r["n"] for r in conn.execute(
                    "SELECT language, COUNT(*) AS n FROM documents "
                    "GROUP BY language ORDER BY n DESC")
            }
            result["by_year"]={  #年份先转字符串再当字典键
                str(r["year"]):r["n"] for r in conn.execute(
                    "SELECT year, COUNT(*) AS n FROM documents WHERE year IS NOT NULL "
                    "GROUP BY year ORDER BY year DESC LIMIT 20")
            }
        except sqlite3.OperationalError as exc:
            result["error"]=str(exc)  #错误串塞进结果里回传，界面照样能渲染
        return result

__all__=["Store","SCHEMA_SQL","SCHEMA_VERSION"]
