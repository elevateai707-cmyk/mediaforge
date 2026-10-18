"""Caption cue construction, quality checks, and text subtitle serializers.

The module is deliberately independent of the database and HTTP layer so a
caption document can be previewed and exported consistently.
"""
from __future__ import annotations

import html
import math
import re
from collections.abc import Iterable, Mapping
from typing import Any


_STYLES = {
    "standard": {"max_chars": 42, "max_lines": 2, "play_res": (1920, 1080), "font_size": 54, "margin_v": 56, "outline": 2, "shadow": 1},
    "social_vertical": {"max_chars": 32, "max_lines": 2, "play_res": (1080, 1920), "font_size": 54, "margin_v": 240, "outline": 3, "shadow": 1},
    "accessibility": {"max_chars": 42, "max_lines": 2, "play_res": (1920, 1080), "font_size": 60, "margin_v": 64, "outline": 3, "shadow": 2},
}
_MIN_DURATION = 0.8
_MAX_DURATION = 7.0
_NOMINAL_CPS = 17.0


def _field(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _number(value: Any) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _clean_text(value: Any) -> str:
    """Make text safe as a single caption payload without dropping content."""
    if value is None:
        return ""
    # Subtitle cue text cannot safely contain structural blank lines. Retain
    # visible text while collapsing arbitrary source whitespace to spaces.
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(value))
    return re.sub(r"\s+", " ", text).strip()


def _wrap_words(words: list[str], width: int, max_lines: int) -> list[str]:
    """Split words into payloads that render as at most ``max_lines`` lines."""
    chunks: list[str] = []
    current: list[str] = []

    def line_count(parts: list[str]) -> int:
        lines, length = 1, 0
        for part in parts:
            added = len(part) + (1 if length else 0)
            if length and length + added > width:
                lines += 1
                length = len(part)
            else:
                length += added
        return lines

    for word in words:
        # An over-wide indivisible word remains intact; it is reported by QC.
        if current and line_count(current + [word]) > max_lines:
            chunks.append(" ".join(current))
            current = []
        current.append(word)
    if current:
        chunks.append(" ".join(current))
    return chunks


def _display_text(text: str, width: int, max_lines: int) -> str:
    """Add display line breaks; chunking has already bounded cue length."""
    words = text.split()
    lines: list[str] = []
    line: list[str] = []
    size = 0
    for word in words:
        added = len(word) + (1 if line else 0)
        if line and size + added > width and len(lines) + 1 < max_lines:
            lines.append(" ".join(line))
            line, size = [word], len(word)
        else:
            line.append(word)
            size += added
    if line:
        lines.append(" ".join(line))
    return "\n".join(lines)


def _valid_words(raw_words: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_words, list):
        return []
    words: list[dict[str, Any]] = []
    for raw in raw_words:
        text = _clean_text(_field(raw, "word", _field(raw, "text", "")))
        start, end = _number(_field(raw, "start")), _number(_field(raw, "end"))
        if text and start is not None and end is not None:
            words.append({"word": text, "start": start, "end": end})
    return words


def _issues_for_cues(cues: list[dict[str, Any]], width: int, max_lines: int) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    prior_end: float | None = None
    for index, cue in enumerate(cues):
        start, end, text = cue["start"], cue["end"], cue["text"]
        duration = end - start
        if duration < _MIN_DURATION:
            issues.append({"cue_index": index, "code": "short_duration", "message": "Cue is shorter than 0.8 seconds."})
        if duration > _MAX_DURATION:
            issues.append({"cue_index": index, "code": "long_duration", "message": "Cue is longer than 7 seconds."})
        cps = len(text.replace("\n", " ")) / duration if duration > 0 else float("inf")
        if cps > _NOMINAL_CPS:
            issues.append({"cue_index": index, "code": "high_cps", "message": f"Cue exceeds nominal {_NOMINAL_CPS:g} characters per second."})
        lines = text.split("\n")
        if len(lines) > max_lines or any(len(line) > width for line in lines):
            issues.append({"cue_index": index, "code": "line_length", "message": f"Cue exceeds the {width}-character, {max_lines}-line preset target."})
        if prior_end is not None:
            if start < prior_end:
                issues.append({"cue_index": index, "code": "overlap", "message": "Cue overlaps the previous cue."})
            elif start - prior_end < 0.08:
                issues.append({"cue_index": index, "code": "tight_gap", "message": "Gap before this cue is shorter than 80 milliseconds."})
        prior_end = end
    return issues


def build_document(segments: Iterable[Any], style: str = "standard") -> dict[str, Any]:
    """Build normalized caption cues and explicit QC findings.

    Word timings are used when every timing needed for a chunk is valid. With
    historical segment-only input, text is split proportionally across the
    source segment and the document explicitly records that limitation.
    """
    if style not in _STYLES:
        raise ValueError(f"unsupported caption style: {style}")
    settings = _STYLES[style]
    width, max_lines = settings["max_chars"], settings["max_lines"]
    cues: list[dict[str, Any]] = []
    source_issues: list[tuple[dict[str, Any], str, str]] = []
    used_word_timing = False
    fell_back_to_segments = False

    for segment in segments:
        text = _clean_text(_field(segment, "text", ""))
        if not text:
            continue
        start, end = _number(_field(segment, "start")), _number(_field(segment, "end"))
        segment_messages: list[tuple[str, str]] = []
        if start is None:
            start = cues[-1]["end"] if cues else 0.0
            segment_messages.append(("invalid_start", "Missing or invalid segment start was repaired."))
        elif start < 0:
            start = 0.0
            segment_messages.append(("invalid_start", "Negative segment start was clamped to zero."))
        if end is None or end <= start:
            end = start + _MIN_DURATION
            segment_messages.append(("invalid_timing", "Missing or invalid segment end was repaired."))

        raw_words = _valid_words(_field(segment, "words", []))
        tokens = text.split()
        chunks = _wrap_words(tokens, width, max_lines)
        # Faster-whisper words normally cover every textual token. Only use
        # them when their sequence does too, avoiding silent text loss.
        word_text = " ".join(word["word"] for word in raw_words)
        can_use_words = (
            bool(raw_words)
            and len(raw_words) == len(tokens)
            and _clean_text(word_text) == text
        )
        bounded_words: list[dict[str, Any]] = []
        if can_use_words:
            previous_end = start
            changed = False
            for word in raw_words:
                word_start = max(start, word["start"])
                word_end = min(end, word["end"])
                changed = changed or word_start != word["start"] or word_end != word["end"]
                if word_start < previous_end or word_end <= word_start:
                    can_use_words = False
                    break
                bounded_words.append({"word": word["word"], "start": word_start, "end": word_end})
                previous_end = word_end
            if changed:
                segment_messages.append(("word_timing_clamped", "Word timing outside its segment was clamped to segment bounds."))
        elif _field(segment, "words", []):
            segment_messages.append(("invalid_word_timing", "Incomplete or malformed word timing was not used."))
        if can_use_words:
            used_word_timing = True
            offset = 0
            for chunk in chunks:
                count = len(chunk.split())
                subset = bounded_words[offset:offset + count]
                offset += count
                cue_start, cue_end = subset[0]["start"], subset[-1]["end"]
                cues.append({"start": cue_start, "end": cue_end, "text": _display_text(chunk, width, max_lines)})
        else:
            fell_back_to_segments = True
            weights = [max(1, len(re.sub(r"\s", "", part))) for part in chunks]
            total = sum(weights)
            cursor = start
            duration = end - start
            for position, (chunk, weight) in enumerate(zip(chunks, weights)):
                cue_end = end if position == len(chunks) - 1 else cursor + duration * weight / total
                cues.append({"start": cursor, "end": cue_end, "text": _display_text(chunk, width, max_lines)})
                cursor = cue_end
        if segment_messages and cues:
            for code, message in segment_messages:
                source_issues.append((cues[-len(chunks)], code, message))

    cues.sort(key=lambda cue: (cue["start"], cue["end"]))
    cue_indexes = {id(cue): index for index, cue in enumerate(cues)}
    issues = [
        {"cue_index": cue_indexes[id(cue)], "code": code, "message": message}
        for cue, code, message in source_issues
    ] + _issues_for_cues(cues, width, max_lines)
    limitations: list[str] = []
    if fell_back_to_segments:
        limitations.append("Some cues use segment timing because complete word timing was unavailable.")
    if not used_word_timing and cues:
        limitations.append("Word-level timing is unavailable; cue boundaries are estimated from segment timing.")
    qc: dict[str, Any] = {"cue_count": len(cues), "issue_count": len(issues), "issues": issues}
    if used_word_timing:
        qc["timing_source"] = "word_timing" if not fell_back_to_segments else "mixed"
    elif cues:
        qc["timing_source"] = "segment_timing"
    if limitations:
        qc["limitations"] = limitations
    return {"style": style, "cues": cues, "qc": qc}


def _timestamp(seconds: float, separator: str = ",") -> str:
    millis = max(0, round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    whole, millis = divmod(millis, 1000)
    return f"{hours:02}:{minutes:02}:{whole:02}{separator}{millis:03}"


def _srt_text(text: str) -> str:
    return "\n".join(html.escape(_clean_text(line), quote=False) for line in text.splitlines())


def _vtt_text(text: str) -> str:
    # Escape markup so WebVTT cue text cannot introduce tags, classes, or
    # timestamp markup. Newlines stay cue-internal, never structural blanks.
    return "\n".join(html.escape(_clean_text(line), quote=False) for line in text.splitlines())


def _ass_text(text: str) -> str:
    # ASS braces and backslashes can form override tags (including \N and \h).
    # Fullwidth equivalents retain visible source text without giving libass
    # parser control syntax. Commas are safe in Dialogue's final text field.
    def neutralize(line: str) -> str:
        return _clean_text(line).translate(str.maketrans({"\\": "＼", "{": "｛", "}": "｝"}))
    return "\\N".join(neutralize(line) for line in text.splitlines())


def serialize(document: Mapping[str, Any], format: str) -> str:
    """Serialize a caption document to SRT, WebVTT, or ASS without file I/O."""
    cues = document.get("cues", [])
    if format == "srt":
        blocks = []
        for index, cue in enumerate(cues, 1):
            blocks.append(f"{index}\n{_timestamp(cue['start'])} --> {_timestamp(cue['end'])}\n{_srt_text(cue['text'])}")
        return "\n\n".join(blocks) + ("\n" if blocks else "")
    if format == "vtt":
        blocks = ["WEBVTT"]
        for cue in cues:
            blocks.append(f"{_timestamp(cue['start'], '.')} --> {_timestamp(cue['end'], '.')}\n{_vtt_text(cue['text'])}")
        return "\n\n".join(blocks) + "\n"
    if format == "ass":
        preset = _STYLES.get(str(document.get("style", "standard")), _STYLES["standard"])
        play_x, play_y = preset["play_res"]
        header = (
            f"[Script Info]\nScriptType: v4.00+\nPlayResX: {play_x}\nPlayResY: {play_y}\n"
            "\n[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
            f"Style: Default,Arial,{preset['font_size']},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,{preset['outline']},{preset['shadow']},2,40,40,{preset['margin_v']},1\n"
            "\n[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
        )
        def ass_time(value: float) -> str:
            centiseconds = max(0, round(value * 100))
            hours, centiseconds = divmod(centiseconds, 360_000)
            minutes, centiseconds = divmod(centiseconds, 6_000)
            seconds, centiseconds = divmod(centiseconds, 100)
            return f"{hours}:{minutes:02}:{seconds:02}.{centiseconds:02}"
        return header + "".join(
            f"Dialogue: 0,{ass_time(cue['start'])},{ass_time(cue['end'])},Default,,0,0,0,,{_ass_text(cue['text'])}\n"
            for cue in cues
        )
    raise ValueError(f"unsupported subtitle format: {format}")
