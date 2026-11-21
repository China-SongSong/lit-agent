"""引用格式化入口的 HTML 清洗回归。"""
from __future__ import annotations

import unittest

from litagent.cite.gbt7714 import format_all
from litagent.cite.guji import format_guji_all


class CitationCleanupTests(unittest.TestCase):
    def test_gbt_formats_strip_tags_and_decode_entities(self)->None:
        doc={
            "id":"doi:test",
            "title":"<i>Evidence</i> &amp; Provenance",
            "authors":[{"family":"<i>Zhang</i>","given":"J","raw":"<i>Zhang</i> J"}],
            "year":2025,
            "venue":"<b>Press</b>",
            "venue_type":"journal",
        }
        formats=format_all(doc)
        joined="\n".join(formats.values())
        self.assertIn("Evidence & Provenance".lower(),joined.lower())
        self.assertNotIn("&amp;",joined)
        self.assertNotIn("<i>",joined)
        self.assertNotIn("<b>",joined)

    def test_guji_formats_decode_entities(self)->None:
        doc={
            "id":"guji:test",
            "book_title":"文史 &amp; 考据",
            "author":"章学诚",
            "publisher":"<i>中华书局</i>",
            "publisher_place":"北京",
            "publish_year":2000,
            "page":"12",
            "extra":{"kind":"guji","collator":"王重民"},
        }
        formats=format_guji_all(doc)
        joined="\n".join(formats.values())
        self.assertIn("文史 & 考据",joined)
        self.assertNotIn("&amp;",joined)
        self.assertNotIn("<i>",joined)


if __name__=="__main__":
    unittest.main()
