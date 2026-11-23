"""OpenCLI 子进程适配器与降级测试。"""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from litagent.config import Settings
from litagent.fetch.opencli import (
    OpenCLISource,
    chinese_metadata_query,
    exact_quote_query,
    parse_cli_payload,
    row_to_document,
)


class OpenCLITests(unittest.TestCase):
    def setUp(self)->None:
        OpenCLISource._failures.clear()
        OpenCLISource._disabled_until.clear()

    def test_parse_wrapped_json_payload(self)->None:
        payload={"ok":True,"data":{"items":[{"title":"A paper"}]}}
        self.assertEqual(parse_cli_payload(json.dumps(payload)),[{"title":"A paper"}])

    def test_row_mapping_uses_browser_source_tier(self)->None:
        doc=row_to_document({
            "title":"知识图谱增强推荐",
            "authors":"张三, 李四",
            "source":"计算机学报",
            "year":"2024",
            "cited":"12",
            "url":"https://example.org/paper",
        },"wanfang")
        self.assertIsNotNone(doc)
        self.assertEqual(doc.source,"opencli:wanfang")
        self.assertEqual(doc.year,2024)
        self.assertEqual(doc.citation_count,12)
        self.assertEqual([a.display for a in doc.authors],["张三","李四"])
        self.assertEqual(doc.extra["candidate_tier"],"browser_supplement")

    def test_site_selection_is_language_and_topic_aware(self)->None:
        self.assertEqual(
            OpenCLISource.select_sites("图神经网络推荐系统"),
            ("cnki","wanfang","baidu-scholar"),
        )
        self.assertEqual(
            OpenCLISource.select_sites("citation tracing"),
            ("google-scholar",),
        )

    def test_chinese_sites_use_metadata_and_exact_query_plans(self)->None:
        quote=(
            "数字孪生技术通过构建物理实体的虚拟映射，实现对制造过程的"
            "实时监测与优化，为复杂系统提供新的研究范式。"
            "制度构造包括“资格确认—状态维持—水平衡量”三个要件。"
        )
        metadata=chinese_metadata_query(quote)
        exact=exact_quote_query(quote)
        self.assertIn("数字",metadata)
        self.assertIn("孪生",metadata)
        self.assertIn("资格确认",metadata)
        self.assertIn("状态维持",metadata)
        self.assertIn("水平衡量",metadata)
        self.assertLessEqual(len(metadata.split()),6)
        self.assertTrue(exact.startswith('"'))
        self.assertTrue(exact.endswith('"'))
        self.assertLessEqual(len(exact),50)
        self.assertEqual(OpenCLISource.query_for_site("cnki",quote),metadata)
        self.assertEqual(OpenCLISource.query_for_site("baidu-scholar",quote),exact)

    def test_local_cnki_adapter_uses_current_domestic_endpoint(self)->None:
        adapter=(
            Path(__file__).resolve().parents[2]
            /"vendor"/"opencli-adapters"/"cnki"/"search.js"
        )
        source=adapter.read_text(encoding="utf-8")
        self.assertIn("kns.cnki.net/kns8s/search",source)
        self.assertNotIn("oversea.cnki.net/kns/search",source)

    def test_search_uses_json_command_and_cache(self)->None:
        response={"ok":True,"data":[{"title":"Cached paper","year":2025}]}
        completed=subprocess.CompletedProcess(
            args=[],returncode=0,stdout=json.dumps(response),stderr=""
        )
        with tempfile.TemporaryDirectory(prefix="litagent-opencli-") as temp:
            settings=Settings(
                data_dir=Path(temp),
                deps_dir=Path(temp)/"deps",
                opencli_command="opencli",
            )
            source=OpenCLISource(settings)
            with patch.object(OpenCLISource,"availability",return_value=type(
                "Status",(),{"available":True,"detail":"ok"}
            )()), patch.object(
                OpenCLISource,"bridge_status",return_value=(True,"connected")
            ), patch.object(source,"_run",return_value=completed) as run:
                first,_=source.search_with_errors("cached query",limit=2)
                second,_=source.search_with_errors("cached query",limit=2)
            self.assertEqual(len(first),1)
            self.assertEqual(len(second),1)
            self.assertEqual(run.call_count,1)

    def test_missing_runtime_returns_error_without_exception(self)->None:
        with tempfile.TemporaryDirectory(prefix="litagent-opencli-") as temp:
            source=OpenCLISource(Settings(
                data_dir=Path(temp),
                deps_dir=Path(temp)/"deps",
                opencli_command="definitely-not-installed-opencli",
            ))
            documents,errors=source.search_with_errors("test",limit=2)
        self.assertEqual(documents,[])
        self.assertTrue(errors)

    def test_bundled_cli_is_preferred_over_npx(self)->None:
        status=OpenCLISource(Settings()).availability()
        self.assertTrue(status.available)
        self.assertIn("项目内置固定版本",status.detail)

    def test_site_circuit_breaker_skips_repeated_failures(self)->None:
        failed=subprocess.CompletedProcess(
            args=[],returncode=1,stdout="",stderr="browser bridge unavailable"
        )
        with tempfile.TemporaryDirectory(prefix="litagent-opencli-") as temp:
            settings=Settings(
                data_dir=Path(temp),
                deps_dir=Path(temp)/"deps",
                opencli_command="opencli",
                opencli_circuit_failures=1,
                opencli_circuit_cooldown_seconds=60,
                opencli_cache_ttl_seconds=0,
            )
            source=OpenCLISource(settings)
            with patch.object(source,"availability",return_value=type(
                "Status",(),{"available":True,"detail":"ok"}
            )()), patch.object(source,"_run",return_value=failed) as run:
                with self.assertRaises(Exception):
                    source._search_site("wanfang","test",1)
                with self.assertRaises(Exception):
                    source._search_site("wanfang","test",1)
            self.assertEqual(run.call_count,1)

    def test_disconnected_bridge_fails_fast_with_actionable_message(self)->None:
        source=OpenCLISource(Settings())
        with patch.object(OpenCLISource,"availability",return_value=type(
            "Status",(),{"available":True,"detail":"ok"}
        )()), patch.object(
            OpenCLISource,
            "bridge_status",
            return_value=(False,"Browser Bridge 扩展未连接"),
        ), patch.object(source,"_run") as run:
            documents,errors=source.search_with_errors("社会权",limit=2)
        self.assertEqual(documents,[])
        self.assertIn("未连接",errors[0])
        run.assert_not_called()


if __name__=="__main__":
    unittest.main()
