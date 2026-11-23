"""构建隔离的评测语料版本，不修改生产数据库。"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Sequence

from litagent.clean.pipeline import CleanPipeline
from litagent.config import Settings
from litagent.fetch.runner import FetchRunner
from litagent.index.builder import IndexBuilder
from litagent.index.store import Store

BIBLIOGRAPHIC_TABLES:Sequence[str]=(
    "documents",
    "documents_fulltext",
)

# 只摘要语料语义字段；抽取时间、清洗时间、总结时间和本地路径不是语料本身。
BIBLIOGRAPHIC_COLUMNS:Dict[str,Sequence[str]]={
    "documents":(
        "id","doi","title","title_en","authors_json","year","venue",
        "venue_type","volume","issue","pages","publisher","publisher_place",
        "edition","isbn","url","access_date","language","abstract",
        "keywords_json","source","source_id","license","is_oa",
        "references_json","content_hash","extra_json","title_norm",
        "has_fulltext",
    ),
    "documents_fulltext":("doc_id","text"),
}


def _sha256(path:Path)->str:
    digest=hashlib.sha256()
    with open(path,"rb") as handle:
        for block in iter(lambda:handle.read(1024*1024),b""):
            digest.update(block)
    return digest.hexdigest()


def _json_value(value:object)->object:
    if isinstance(value,bytes):
        return{"blob_hex":value.hex()}
    return value


def logical_database_table_sha256(path:Path)->Dict[str,str]:
    """按书目/正文表计算稳定摘要，排除总结、缓存、日志和元数据。"""
    uri=path.resolve().as_uri()+"?mode=ro"
    conn=sqlite3.connect(uri,uri=True)
    result:Dict[str,str]={}
    try:
        existing={
            str(row[0]) for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        for table in BIBLIOGRAPHIC_TABLES:
            if table not in existing:
                continue
            available={
                str(row[1])
                for row in conn.execute(
                    'PRAGMA table_info("%s")'%table
                )
            }
            columns=[
                column for column in BIBLIOGRAPHIC_COLUMNS.get(
                    table,("*",)
                )
                if column=="*" or column in available
            ]
            digest=hashlib.sha256()
            selected=", ".join('"%s"'%column for column in columns)
            for row in conn.execute(
                'SELECT %s FROM "%s" ORDER BY rowid'%(selected,table)
            ):
                payload=json.dumps(
                    [_json_value(value) for value in row],
                    ensure_ascii=False,
                    separators=(",",":"),
                    default=str,
                )
                digest.update(payload.encode("utf-8"))
                digest.update(b"\n")
            result[table]=digest.hexdigest()
    finally:
        conn.close()
    return result


def logical_database_sha256(path:Path)->str:
    """对书目表组合作摘要，忽略 WAL、总结重生成与元数据升级。"""
    digest=hashlib.sha256()
    for table,table_hash in logical_database_table_sha256(path).items():
        digest.update(table.encode("utf-8"))
        digest.update(b"\0")
        digest.update(table_hash.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def snapshot_database(source:Path,destination:Path)->None:
    """用 SQLite backup API 创建一致性只读快照。"""
    source=source.resolve()
    destination=destination.resolve()
    if destination.exists():
        raise FileExistsError("目标评测库已存在：%s"%destination)
    destination.parent.mkdir(parents=True,exist_ok=True)
    uri=source.as_uri()+"?mode=ro"
    src=sqlite3.connect(uri,uri=True)
    dst=sqlite3.connect(str(destination))
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
        src.close()


def build_corpus_version(
    source_db:Path,
    pdf_dir:Path,
    output_dir:Path,
    *,
    version:str,
)->dict:
    """从生产快照补充本地 PDF，并生成可复现的版本清单。"""
    source_db=source_db.resolve()
    pdf_dir=pdf_dir.resolve()
    output_dir=output_dir.resolve()
    target_db=output_dir/"index.db"
    snapshot_database(source_db,target_db)

    settings=Settings(
        project_root=source_db.parent,
        data_dir=output_dir,
        deps_dir=source_db.parent/"deps-unused",
    )
    settings.ensure_dirs()
    store=Store(target_db)
    try:
        store.init_schema()
        store.repair_fulltext_flags()
        runner=FetchRunner(store,settings)
        fetch_report=runner.fetch_local([pdf_dir],recursive=False)
        store.repair_fulltext_flags()
        clean_report=CleanPipeline(store,settings).run(
            redo=False,
            run_dedupe=True,
        )
        index_report=IndexBuilder(store,settings).build_all(rebuild=False)
        stats=store.stats()
    finally:
        store.close_all()

    # SQLite 在关闭连接时可能把 WAL 合并回主库，因此清单哈希必须在关闭后计算。
    source_table_hashes=logical_database_table_sha256(source_db)
    target_table_hashes=logical_database_table_sha256(target_db)

    def combined_hash(table_hashes:Dict[str,str])->str:
        digest=hashlib.sha256()
        for table,table_hash in table_hashes.items():
            digest.update(table.encode("utf-8"))
            digest.update(b"\0")
            digest.update(table_hash.encode("ascii"))
            digest.update(b"\n")
        return digest.hexdigest()

    manifest={
        "corpus_version":version,
        "created_at":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source_db":str(source_db),
        "source_db_physical_sha256":_sha256(source_db),
        "source_db_logical_sha256":combined_hash(source_table_hashes),
        "source_db_table_sha256":source_table_hashes,
        "pdf_dir":str(pdf_dir),
        "database":str(target_db),
        "database_physical_sha256":_sha256(target_db),
        "database_logical_sha256":combined_hash(target_table_hashes),
        "database_table_sha256":target_table_hashes,
        "counts":stats,
        "fetch":{
            "new":fetch_report.new,
            "merged":fetch_report.merged,
            "skipped":fetch_report.skipped,
            "errors":fetch_report.errors,
        },
        "clean":{
            "processed":clean_report.processed,
            "failed":clean_report.failed,
            "chunks_created":clean_report.chunks_created,
            "errors":clean_report.errors,
            "dedupe":(
                clean_report.dedupe.summary()
                if clean_report.dedupe is not None else ""
            ),
        },
        "index":{
            "documents":index_report.docs_processed,
            "chunks":index_report.chunks_indexed,
            "fingerprints":index_report.fingerprints_written,
            "errors":index_report.errors,
        },
    }
    manifest_path=output_dir/"manifest.json"
    manifest_path.write_text(
        json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",
        encoding="utf-8",
    )
    return manifest


def main(argv:Optional[Sequence[str]]=None)->int:
    parser=argparse.ArgumentParser(description="构建 lit-agent 隔离评测语料版本")
    parser.add_argument("--source-db",required=True)
    parser.add_argument("--pdf-dir",required=True)
    parser.add_argument("--output-dir",required=True)
    parser.add_argument("--version",default="evaluation-v2")
    args=parser.parse_args(argv)
    manifest=build_corpus_version(
        Path(args.source_db),
        Path(args.pdf_dir),
        Path(args.output_dir),
        version=args.version,
    )
    print(json.dumps(manifest,ensure_ascii=False,indent=2))
    return 0


if __name__=="__main__":
    raise SystemExit(main())


__all__=[
    "snapshot_database",
    "BIBLIOGRAPHIC_TABLES",
    "BIBLIOGRAPHIC_COLUMNS",
    "logical_database_table_sha256",
    "logical_database_sha256",
    "build_corpus_version",
    "main",
]
