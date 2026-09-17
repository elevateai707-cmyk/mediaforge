"""Final-timeline captions, safe ASS/libass output and explicit sidecars."""

import textwrap

from .project import Cue, Project, Word, timeline


def mapped_cues(project: Project, overlays=False):
    out = []
    if not overlays and project.caption_source == "narration":
        shift = project.voice_over.start
        end = (
            timeline(project)[-1]["start"] + timeline(project)[-1]["duration"]
            if project.clips
            else 0
        )
        for cue in project.narration_captions:
            if cue.start + shift >= end:
                continue
            c = cue.model_copy(deep=True)
            c.start += shift
            c.end = min(end, c.end + shift)
            c.words = [
                Word(start=w.start + shift, end=min(end, w.end + shift), text=w.text)
                for w in c.words
                if w.start + shift < end
            ]
            out.append((c, project.caption_style))
        return out
    for item in timeline(project):
        clip = item["clip"]
        source = (project.overlays if overlays else project.captions).get(clip.uid, [])

        def map_word(w, clip=clip, item=item):
            start = max(clip.start, w.start)
            end = min(clip.end, w.end)
            if end <= start:
                return None
            return Word(
                start=item["start"] + (start - clip.start) / clip.speed,
                end=item["start"] + (end - clip.start) / clip.speed,
                text=w.text,
            )

        for c in source:
            w = map_word(c)
            if w:
                words = [x for x in (map_word(x) for x in c.words) if x]
                out.append(
                    (
                        Cue(**w.model_dump(), words=words),
                        c.style if overlays else project.caption_style,
                    )
                )
    return sorted(out, key=lambda x: x[0].start)


def stamp(t, ass=False, vtt=False):
    n = round(t * (100 if ass else 1000))
    unit = 100 if ass else 1000
    seconds, frac = divmod(n, unit)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return (
        f"{hours}:{minutes:02}:{seconds:02}.{frac:02}"
        if ass
        else f"{hours:02}:{minutes:02}:{seconds:02}{'.' if vtt else ','}{frac:03}"
    )


def safe_ass(s):
    # Strip ASS control syntax while preserving visible Unicode and punctuation.
    return (
        s.replace("\\", "＼")
        .replace("{", "｛")
        .replace("}", "｝")
        .replace("\r", "")
        .replace("\n", r"\N")
    )


def ass_color(s):
    return "&H00" + s[5:7] + s[3:5] + s[1:3]


def ass_document(project, width, height, speech=True, overlays=True):
    entries = []
    if speech:
        entries += mapped_cues(project)
    if overlays:
        entries += mapped_cues(project, True)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    styles = []
    events = []
    for i, (cue, style) in enumerate(entries):
        size = max(12, style.size * height / 1920)
        margin = round(width * style.margin)
        vertical = round(height * style.margin)
        chars = max(5, int((width - 2 * margin) / (size * 0.62)))
        lines = textwrap.wrap(
            cue.text, width=chars, break_long_words=True, break_on_hyphens=False
        ) or [""]
        # Split long cues temporally, never silently drop text to enforce line cap.
        chunks = [
            lines[j : j + style.max_lines]
            for j in range(0, len(lines), style.max_lines)
        ]
        name = f"S{i}"
        align = {"bottom": 2, "center": 5, "top": 8}[style.position]
        styles.append(
            f"Style: {name},{style.font},{size:.2f},{ass_color(style.color)},&H0000FFFF,&H00000000,&H80000000,{-1 if style.preset == 'bold' else 0},0,0,0,100,100,0,0,{3 if style.background else 1},{style.outline},0,{align},{margin},{margin},{vertical},1"
        )
        if style.preset == "active" and cue.words:
            # Active-word preset intentionally displays one timed word at a time.
            for w in cue.words:
                body = safe_ass("\n".join(textwrap.wrap(w.text, width=chars)))
                events.append(
                    f"Dialogue: 0,{stamp(w.start, True)},{stamp(w.end, True)},{name},,0,0,0,,{{\\c&H00FFFF&}}{body}"
                )
        else:
            for j, chunk in enumerate(chunks):
                a = cue.start + (cue.end - cue.start) * j / len(chunks)
                b = cue.start + (cue.end - cue.start) * (j + 1) / len(chunks)
                events.append(
                    f"Dialogue: 0,{stamp(a, True)},{stamp(b, True)},{name},,0,0,0,,{safe_ass(chr(10).join(chunk))}"
                )
    return (
        header.replace("[Events]", "\n".join(styles) + "\n[Events]")
        + "\n".join(events)
        + "\n"
    )


def sidecar(project, kind):
    if kind == "ass":
        width = project.export.width
        height = (
            round(
                width
                * {"9:16": 16 / 9, "16:9": 9 / 16, "1:1": 1}[project.export.ratio]
                / 2
            )
            * 2
        )
        return ass_document(project, width, height, speech=True, overlays=False)
    cues = mapped_cues(project)
    parts = ["WEBVTT\n"] if kind == "vtt" else []
    for i, (cue, _) in enumerate(cues):
        parts.append(
            f"{i + 1}\n{stamp(cue.start, vtt=kind == 'vtt')} --> {stamp(cue.end, vtt=kind == 'vtt')}\n{cue.text.replace(chr(13), '')}\n"
        )
    return "\n".join(parts)


def phrase_cues(words, max_chars=42):
    cues = []
    batch = []
    for w in words:
        if batch and (
            len(" ".join(x.text for x in batch)) + len(w.text) > max_chars
            or w.start - batch[-1].end > 0.65
        ):
            cues.append(
                Cue(
                    start=batch[0].start,
                    end=batch[-1].end,
                    text=" ".join(x.text for x in batch),
                    words=batch,
                )
            )
            batch = []
        batch.append(w)
        if w.text.endswith((".", "?", "!", "。", "？", "！")):
            cues.append(
                Cue(
                    start=batch[0].start,
                    end=batch[-1].end,
                    text=" ".join(x.text for x in batch),
                    words=batch,
                )
            )
            batch = []
    if batch:
        cues.append(
            Cue(
                start=batch[0].start,
                end=batch[-1].end,
                text=" ".join(x.text for x in batch),
                words=batch,
            )
        )
    return cues
