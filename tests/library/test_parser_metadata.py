from library.parsers import _chapter_heading, _printed_page, _text_reliable


def test_chapter_heading_ignores_prose_that_mentions_chapters():
    assert _chapter_heading("第 2 章 线性表\n线性表由数据元素构成") == "第 2 章 线性表"
    assert _chapter_heading("第7章从抽象数据类型的角度，分别讨论线性表、栈和队列") == ""
    assert _chapter_heading("第 4 章页") == ""


def test_printed_page_requires_an_explicit_footer_number():
    assert _printed_page("正文\n20") == 20
    assert _printed_page("本章共20页\n正文") is None


def test_empty_or_visibly_corrupted_extraction_is_unreliable():
    assert not _text_reliable("")
    assert not _text_reliable("普通文字 □ OCR 方框")
    assert not _text_reliable("普通文字\ufffd")
    assert _text_reliable("int *next = node->next;\n")
