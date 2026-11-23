"""URL 预填问题与溯源参数的回归测试。"""
from __future__ import annotations

import unittest
from urllib.parse import parse_qs,urlparse

from litagent.web_links import(
    autorun_allowed,
    build_prefill_url,
    parse_url_prefill,
)


class UrlPrefillTests(unittest.TestCase):
    def test_question_and_generic_query_are_prefilled_without_execution(self)->None:
        prefill=parse_url_prefill({"question":["帮我找几篇论文"]})
        self.assertEqual(prefill.mode,"chat")
        self.assertEqual(prefill.question,"帮我找几篇论文")
        self.assertTrue(prefill.token)
        self.assertEqual(parse_url_prefill({"q":"另一个问题"}).question,"另一个问题")

    def test_trace_mode_uses_q_and_reads_hint_and_online(self)->None:
        prefill=parse_url_prefill({
            "mode":"trace",
            "q":"社会权条款的形成保留和内在限制",
            "hint":"DOI: 10.1000/example",
            "online":"1",
        })
        self.assertEqual(prefill.mode,"trace")
        self.assertEqual(prefill.quote,"社会权条款的形成保留和内在限制")
        self.assertEqual(prefill.source_hint,"DOI: 10.1000/example")
        self.assertTrue(prefill.allow_online)

    def test_control_characters_and_excess_question_length_are_limited(self)->None:
        prefill=parse_url_prefill({
            "question":"有效问题\x00\x01"+"字"*5000,
        })
        self.assertNotIn("\x00",prefill.question)
        self.assertNotIn("\x01",prefill.question)
        self.assertEqual(len(prefill.question),4000)

    def test_prefill_token_is_stable_for_same_payload(self)->None:
        params={"question":"检查这篇论文","mode":"chat"}
        self.assertEqual(
            parse_url_prefill(params).token,
            parse_url_prefill(params).token,
        )

    def test_share_url_round_trips(self)->None:
        url=build_prefill_url(
            "http://127.0.0.1:8501/",mode="trace",
            text="资格确认—状态维持—水平衡量",
            source_hint="CNKI filename=ZGFX202605001",
            allow_online=True,
        )
        query=parse_qs(urlparse(url).query)
        self.assertEqual(query["mode"],["trace"])
        self.assertEqual(query["q"],["资格确认—状态维持—水平衡量"])
        self.assertEqual(query["hint"],["CNKI filename=ZGFX202605001"])
        self.assertEqual(query["online"],["1"])

    def test_autorun_is_explicit_and_token_can_be_enforced(self)->None:
        prefill=parse_url_prefill({
            "question":"自动执行这个问题",
            "autorun":"1",
            "token":"secret-token",
        })
        self.assertTrue(prefill.autorun)
        self.assertEqual(prefill.autorun_token,"secret-token")
        self.assertTrue(autorun_allowed(prefill,""))
        self.assertTrue(autorun_allowed(prefill,"secret-token"))
        self.assertFalse(autorun_allowed(prefill,"wrong-token"))
        self.assertFalse(autorun_allowed(
            parse_url_prefill({"question":"普通预填"}),
        ))

    def test_autorun_share_url_contains_explicit_flag_and_token(self)->None:
        url=build_prefill_url(
            "http://127.0.0.1:8501/",mode="chat",text="自动提问",
            autorun=True,autorun_token="integration-secret",
        )
        query=parse_qs(urlparse(url).query)
        self.assertEqual(query["autorun"],["1"])
        self.assertEqual(query["token"],["integration-secret"])


if __name__=="__main__":
    unittest.main()
