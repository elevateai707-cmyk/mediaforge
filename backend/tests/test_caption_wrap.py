"""Burned captions must be readable: real line breaks, inside the frame.

The reel rendered "Woman in colorful Saint Kittsnand Nevis flag attire",
clipped at both edges. _wrap_text joined lines with a literal backslash-n,
_escape_drawtext doubled the backslash, and ffmpeg's filtergraph parser
collapsed the escape to a bare 'n'. The 40-char wrap was also far wider than
a 1080px frame at fontsize 64.
"""
from __future__ import annotations

from app.edits.render import _chars_per_line, _wrap_text

CAPTION = "Woman in colorful Saint Kitts and Nevis flag attire at outdoor event"


def test_wrap_uses_real_newlines_not_a_literal_backslash_n():
    wrapped = _wrap_text(CAPTION, width=28)
    assert "\n" in wrapped, "must break with a real newline"
    assert "\\n" not in wrapped, "a literal backslash-n renders as 'n'"
    # The exact defect seen in the output video.
    assert "Kittsnand" not in wrapped.replace("\n", "")


def test_no_word_is_split_across_lines():
    wrapped = _wrap_text(CAPTION, width=28)
    assert wrapped.replace("\n", " ").split() == CAPTION.split()


def test_lines_fit_inside_a_1080px_frame():
    fontsize = 1920 // 34
    width = _chars_per_line(1080, fontsize)
    for line in _wrap_text(CAPTION, width=width).split("\n"):
        assert len(line) <= width, f"line overflows: {line!r}"
        # Conservative pixel check against the real frame width.
        assert len(line) * fontsize * 0.52 <= 1080, f"clipped: {line!r}"


def test_chars_per_line_shrinks_as_font_grows():
    assert _chars_per_line(1080, 40) > _chars_per_line(1080, 80)
    assert _chars_per_line(1080, 200) >= 12  # never collapses to zero


def test_empty_caption_is_safe():
    assert _wrap_text("") == ""
    assert _wrap_text("   ") == ""
