"""Regression tests for direct document import from the web UI."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

from litagent.config import Settings
from litagent.fetch.local_pdf import (
    SUPPORTED_SUFFIXES,
    read_docx,
    read_html,
    read_legacy_word,
)
from litagent.importing import LocalDocumentImporter
from litagent.index.store import Store


class DocumentFormatTests(unittest.TestCase):
    def test_supported_formats_include_common_office_and_text_files(self)->None:
        for suffix in (
            ".pdf",
            ".docx",
            ".doc",
            ".rtf",
            ".txt",
            ".md",
            ".markdown",
            ".html",
            ".htm",
        ):
            self.assertIn(suffix,SUPPORTED_SUFFIXES)

    def test_docx_reader_preserves_paragraph_boundaries(self)->None:
        with tempfile.TemporaryDirectory(prefix="litagent-docx-") as temp:
            path=Path(temp)/"sample.docx"
            xml=(
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/'
                'wordprocessingml/2006/main"><w:body>'
                "<w:p><w:r><w:t>第一段正文</w:t></w:r></w:p>"
                "<w:p><w:r><w:t>第二段正文</w:t></w:r></w:p>"
                "</w:body></w:document>"
            )
            with ZipFile(path,"w",ZIP_DEFLATED) as archive:
                archive.writestr("word/document.xml",xml)
            self.assertEqual(read_docx(path),"第一段正文\n第二段正文")

    def test_html_reader_removes_scripts_and_keeps_blocks(self)->None:
        with tempfile.TemporaryDirectory(prefix="litagent-html-") as temp:
            path=Path(temp)/"sample.html"
            path.write_text(
                "<html><body><h1>标题</h1><p>第一段</p>"
                "<script>alert('x')</script><p>第二段</p></body></html>",
                encoding="utf-8",
            )
            text=read_html(path)
            self.assertIn("标题",text)
            self.assertIn("第一段",text)
            self.assertIn("第二段",text)
            self.assertNotIn("alert",text)

    def test_legacy_word_reader_uses_document_converter(self)->None:
        with tempfile.TemporaryDirectory(prefix="litagent-doc-") as temp:
            path=Path(temp)/"legacy.doc"
            path.write_bytes(b"legacy-binary-placeholder")

            def fake_convert(_source:Path,output:Path)->bool:
                output.write_text("旧版 Word 正文",encoding="utf-8")
                return True

            with patch(
                "litagent.fetch.local_pdf._powershell_convert_document",
                side_effect=fake_convert,
            ):
                self.assertEqual(read_legacy_word(path),"旧版 Word 正文")


class DirectImportTests(unittest.TestCase):
    def _settings(self,temp:Path)->Settings:
        return Settings(
            project_root=temp,
            data_dir=temp/"data",
            vector_enabled=False,
        )

    @staticmethod
    def _write_docx(path:Path,paragraphs:list[str])->None:
        body="".join(
            "<w:p><w:r><w:t>%s</w:t></w:r></w:p>"%text
            for text in paragraphs
        )
        xml=(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/'
            'wordprocessingml/2006/main"><w:body>'
            +body+
            "</w:body></w:document>"
        )
        with ZipFile(path,"w",ZIP_DEFLATED) as archive:
            archive.writestr("word/document.xml",xml)

    def test_import_folder_cleans_and_indexes_without_extra_commands(self)->None:
        with tempfile.TemporaryDirectory(prefix="litagent-import-") as temp_dir:
            temp=Path(temp_dir)
            source_dir=temp/"documents"
            source_dir.mkdir()
            (source_dir/"notes.txt").write_text(
                "引文溯源需要区分原文证据与语义相似。"
                "精确指纹用于确认连续文本重合，语义向量只能扩大候选范围。",
                encoding="utf-8",
            )
            self._write_docx(
                source_dir/"paper.docx",
                [
                    "这是一篇用于网页导入测试的论文。",
                    "系统应在导入后自动清洗正文并建立精确指纹与关键词索引。",
                ],
            )
            settings=self._settings(temp)
            with Store(settings.db_path) as store:
                store.init_schema()

                report=LocalDocumentImporter(store,settings).import_paths([source_dir])

                self.assertEqual(report.files_discovered,2)
                self.assertEqual(report.documents_imported,2)
                self.assertEqual(report.documents_cleaned,2)
                self.assertGreater(report.chunks_created,0)
                self.assertGreater(report.postings_written,0)
                self.assertGreater(report.fingerprints_written,0)
                self.assertEqual(store.count_documents(),2)

    def test_uploaded_text_file_is_stored_and_indexed(self)->None:
        class Upload:
            name="uploaded.txt"

            @staticmethod
            def getbuffer():
                return memoryview(
                    "上传文本也需要自动清洗并进入本地检索索引。".encode("utf-8")
                )

        with tempfile.TemporaryDirectory(prefix="litagent-upload-") as temp_dir:
            temp=Path(temp_dir)
            settings=self._settings(temp)
            with Store(settings.db_path) as store:
                store.init_schema()

                report=LocalDocumentImporter(store,settings).import_uploads([Upload()])

                self.assertEqual(report.documents_imported,1)
                self.assertEqual(report.documents_cleaned,1)
                self.assertEqual(store.count_documents(),1)
                self.assertTrue(any((temp/"data"/"imports").rglob("uploaded.txt")))


if __name__=="__main__":
    unittest.main()
