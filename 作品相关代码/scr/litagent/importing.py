"""One-step local document import, cleaning and indexing."""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, List, Optional, Sequence

from litagent.clean.pipeline import CleanPipeline
from litagent.config import Settings
from litagent.config import settings as default_settings
from litagent.fetch.local_pdf import SUPPORTED_SUFFIXES, LocalPdfSource
from litagent.fetch.runner import FetchRunner
from litagent.index.builder import IndexBuilder
from litagent.index.store import Store

ProgressCallback = Callable[[str, int, int, str], None]


@dataclass
class ImportReport:
    """Result of one local import transaction."""

    files_discovered: int = 0
    documents_scanned: int = 0
    documents_imported: int = 0
    documents_updated: int = 0
    documents_cleaned: int = 0
    documents_skipped: int = 0
    chunks_created: int = 0
    postings_written: int = 0
    fingerprints_written: int = 0
    vectors_written: int = 0
    failed_files: int = 0
    imported_ids: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    def summary(self) -> str:
        lines = [
            "文档导入完成",
            f"  发现文件：{self.files_discovered}",
            f"  新增文献：{self.documents_imported}",
            f"  更新文献：{self.documents_updated}",
            f"  清洗成功：{self.documents_cleaned}",
            f"  跳过文献：{self.documents_skipped}",
            f"  新建片段：{self.chunks_created}",
            f"  BM25 倒排：{self.postings_written}",
            f"  精确指纹：{self.fingerprints_written}",
            f"  语义向量：{self.vectors_written}",
            f"  失败文件：{self.failed_files}",
            f"  总耗时：{self.elapsed_seconds:.1f} 秒",
        ]
        if self.errors:
            lines.append("  错误样例：")
            lines.extend(f"    - {error}" for error in self.errors[:8])
        return "\n".join(lines)


def _safe_filename(name: str) -> str:
    """Keep a readable basename while removing Windows-reserved characters."""
    basename = Path(name or "").name.strip()
    basename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", basename)
    basename = basename.strip(" .")
    return basename or "uploaded-document"


class LocalDocumentImporter:
    """Import local files and run fetch, clean and index as one operation."""

    def __init__(self, store: Store, settings: Optional[Settings] = None) -> None:
        self.store = store
        self.settings = settings or default_settings
        self.settings.ensure_dirs()
        self.source = LocalPdfSource(self.settings)
        self.runner = FetchRunner(store, self.settings)
        self.cleaner = CleanPipeline(store, self.settings)
        self.indexer = IndexBuilder(store, self.settings)

    @staticmethod
    def supported_extensions() -> List[str]:
        return sorted(suffix.lstrip(".") for suffix in SUPPORTED_SUFFIXES)

    def import_paths(
        self,
        paths: Sequence[Any],
        *,
        recursive: bool = True,
        on_progress: Optional[ProgressCallback] = None,
        max_files: int = 500,
    ) -> ImportReport:
        """Import a file or directory and index all affected documents."""
        started = time.monotonic()
        report = ImportReport()

        discovered = self.source.discover(paths, recursive=recursive)
        if len(discovered) > max_files:
            report.failed_files += len(discovered) - max_files
            report.errors.append(
                f"单次最多导入 {max_files} 个文件，已忽略后 "
                f"{len(discovered) - max_files} 个文件"
            )
            discovered = discovered[:max_files]
        report.files_discovered = len(discovered)
        if not discovered:
            report.elapsed_seconds = time.monotonic() - started
            return report

        def scan_progress(done: int, total: int, path: Any) -> None:
            if on_progress:
                on_progress("scan", done, total, Path(path).name)

        scanned = self.source.scan(
            discovered,
            recursive=False,
            on_progress=scan_progress,
        )
        report.documents_scanned = len(scanned)

        valid_documents = []
        for document in scanned:
            error = str((document.extra or {}).get("error") or "").strip()
            if error:
                report.failed_files += 1
                report.errors.append(f"{document.title}: {error}")
            else:
                valid_documents.append(document)

        incoming = self.runner.ingest(valid_documents)
        report.documents_imported = incoming.new
        report.documents_updated = incoming.merged
        report.documents_skipped += incoming.skipped
        report.imported_ids = list(
            dict.fromkeys(incoming.new_ids + incoming.updated_ids)
        )
        report.errors.extend(incoming.errors)

        if report.imported_ids:
            def clean_progress(done: int, total: int, document: Any) -> None:
                if on_progress:
                    on_progress("clean", done, total, document.title)

            cleaned = self.cleaner.run(
                doc_ids=report.imported_ids,
                redo=True,
                on_progress=clean_progress,
            )
            report.documents_cleaned = cleaned.processed
            report.documents_skipped += cleaned.skipped
            report.chunks_created = cleaned.chunks_created
            report.failed_files += cleaned.failed
            report.errors.extend(cleaned.errors)

            def index_progress(stage: str, done: int, total: int) -> None:
                if on_progress:
                    on_progress(stage, done, total, "")

            indexed = self.indexer.build_all(
                doc_ids=report.imported_ids,
                rebuild=True,
                on_progress=index_progress,
            )
            report.postings_written = indexed.postings_written
            report.fingerprints_written = indexed.fingerprints_written
            report.vectors_written = indexed.vectors_written
            report.failed_files += len(indexed.errors)
            report.errors.extend(indexed.errors)

        report.elapsed_seconds = time.monotonic() - started
        return report

    def import_uploads(
        self,
        uploads: Iterable[Any],
        *,
        on_progress: Optional[ProgressCallback] = None,
    ) -> ImportReport:
        """Copy browser uploads to a stable managed path and import them."""
        started = time.monotonic()
        report = ImportReport()
        import_root = self.settings.data_dir / "imports"
        import_root.mkdir(parents=True, exist_ok=True)
        saved_paths: List[Path] = []

        for upload in uploads:
            name = _safe_filename(getattr(upload, "name", ""))
            suffix = Path(name).suffix.lower()
            if suffix not in SUPPORTED_SUFFIXES:
                report.failed_files += 1
                report.errors.append(f"{name}: 不支持的文件格式")
                continue
            try:
                if hasattr(upload, "getbuffer"):
                    data = bytes(upload.getbuffer())
                else:
                    data = bytes(upload.read())
            except Exception as exc:  # noqa: BLE001
                report.failed_files += 1
                report.errors.append(f"{name}: 读取上传内容失败：{exc}")
                continue
            if not data:
                report.failed_files += 1
                report.errors.append(f"{name}: 文件为空")
                continue

            digest = hashlib.sha256(data).hexdigest()[:20]
            target_dir = import_root / digest
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / name
            if not target.is_file() or target.stat().st_size != len(data):
                target.write_bytes(data)
            saved_paths.append(target)

        if not saved_paths:
            report.elapsed_seconds = time.monotonic() - started
            return report

        result = self.import_paths(
            saved_paths,
            recursive=False,
            on_progress=on_progress,
            max_files=max(1, len(saved_paths)),
        )
        result.failed_files += report.failed_files
        result.errors = report.errors + result.errors
        result.elapsed_seconds = time.monotonic() - started
        return result


__all__ = ["ImportReport", "LocalDocumentImporter"]
