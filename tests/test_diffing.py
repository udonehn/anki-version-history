from __future__ import annotations

from note_version_history import diffing
from note_version_history.diffing import DELETE, EQUAL, INSERT


def kinds(spans):
    return [span.kind for span in spans]


def test_identical_content_is_one_equal_span():
    spans = diffing.word_diff("hello world", "hello world")
    assert kinds(spans) == [EQUAL]
    assert spans[0].text == "hello world"


def test_insertion_detected():
    spans = diffing.word_diff("hello world", "hello brave world")
    inserted = "".join(s.text for s in spans if s.kind == INSERT)
    assert "brave" in inserted
    assert "".join(s.text for s in spans if s.kind != DELETE) == "hello brave world"


def test_deletion_detected():
    spans = diffing.word_diff("hello brave world", "hello world")
    deleted = "".join(s.text for s in spans if s.kind == DELETE)
    assert "brave" in deleted


def test_replace_produces_delete_then_insert():
    spans = diffing.word_diff("color red", "color blue")
    assert DELETE in kinds(spans)
    assert INSERT in kinds(spans)
    reconstructed_new = "".join(s.text for s in spans if s.kind in (EQUAL, INSERT))
    assert reconstructed_new == "color blue"


def test_html_tags_are_atomic_tokens():
    spans = diffing.word_diff('<b>bold</b>', '<i>bold</i>')
    deleted = "".join(s.text for s in spans if s.kind == DELETE)
    inserted = "".join(s.text for s in spans if s.kind == INSERT)
    assert "<b>" in deleted and "<i>" in inserted


def test_spans_to_html_escapes_everything():
    old = '<script>alert("x")</script>'
    new = '<img src=x onerror=alert(1)> safe'
    html_out = diffing.spans_to_html(diffing.word_diff(old, new))
    assert "<script" not in html_out
    assert "<img" not in html_out
    assert "&lt;script&gt;" in html_out
    assert "&lt;img" in html_out
    # only our own span markup is present as real tags
    assert html_out.count("<span") == html_out.count("</span>")


def test_newlines_render_as_br():
    html_out = diffing.spans_to_html(diffing.word_diff("a\nb", "a\nb"))
    assert "<br>" in html_out


def test_large_content_falls_back_to_line_diff():
    old = "\n".join(f"line number {i}" for i in range(5000))
    new = old.replace("line number 2000", "line number 2000 CHANGED")
    assert len(old) > diffing.LARGE_CONTENT_CHARS
    spans = diffing.word_diff(old, new)
    inserted = "".join(s.text for s in spans if s.kind == INSERT)
    assert "CHANGED" in inserted
    # line mode: the changed region is whole lines
    assert "line number 2000 CHANGED" in inserted


def test_unified_text_diff_shape():
    old = ".card { color: black; }\n.extra { margin: 0; }\n"
    new = ".card { color: red; }\n.extra { margin: 0; }\n"
    text = diffing.unified_text_diff(old, new, "v1", "v2")
    assert "--- v1" in text
    assert "+++ v2" in text
    assert "@@" in text
    assert "-.card { color: black; }" in text
    assert "+.card { color: red; }" in text


def test_plain_to_html_escapes_no_diff_markup():
    out = diffing.plain_to_html('<b>x</b>\n<script>alert(1)</script>')
    assert "<b>" not in out and "<script" not in out
    assert "&lt;b&gt;" in out
    assert "<br>" in out  # newline preserved
    assert "<span" not in out  # no diff highlighting


def test_plain_to_html_monospace_wrapper():
    out = diffing.plain_to_html(".card { color: red; }", monospace=True)
    assert "font-family:monospace" in out
    assert "&lt;" not in out  # nothing to escape here
    assert diffing.plain_to_html("", monospace=True).endswith("&nbsp;</div>")


def test_unified_to_html_colors_and_escapes():
    old = "<style>.card { color: black; }</style>\n"
    new = "<style>.card { color: red; }</style>\n"
    text = diffing.unified_text_diff(old, new, "v1", "v2")
    html_out = diffing.unified_to_html(text)
    assert "<style>" not in html_out  # escaped
    assert "&lt;style&gt;" in html_out
    assert 'style="background-color:#d0f0d0;"' in html_out  # + line colored
    assert 'style="background-color:#f0d0d0;"' in html_out  # - line colored
    assert "font-family:monospace" in html_out


def test_full_replacement_merges_into_two_spans():
    # no shared tokens (not even whitespace) → one DELETE + one INSERT
    spans = diffing.word_diff("abc", "xyz")
    assert kinds(spans) == [DELETE, INSERT]


def test_contiguous_tokens_merge_into_one_span():
    spans = diffing.word_diff("aaa bbb", "")
    assert kinds(spans) == [DELETE]
    assert spans[0].text == "aaa bbb"

    spans = diffing.word_diff("", "aaa bbb")
    assert kinds(spans) == [INSERT]
    assert spans[0].text == "aaa bbb"
