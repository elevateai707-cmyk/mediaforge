"""Final-timeline captions, safe ASS/libass output and explicit sidecars."""

import functools
import re
import struct
import subprocess
import textwrap

from ..proc import tool_env
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
    if overlays and project.clips:
        tl = timeline(project)
        end = tl[-1]["start"] + tl[-1]["duration"]
        for o in project.timeline_overlays:
            if o.start >= end:
                continue
            c = o.model_copy(deep=True)
            c.end = min(end, c.end)
            out.append((c, o.style))
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
    underlay = []
    for i, (cue, style) in enumerate(entries):
        kind = getattr(cue, "kind", "text")
        if kind == "inset":
            continue  # composited by render_v2, not libass
        if kind in ("solid", "checklist"):
            name = f"S{i}"
            styles.append(
                f"Style: {name},{style.font},{max(12, style.size * height / 1920):.2f},{ass_color(style.color)},&H0000FFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1"
            )
            if kind == "solid":
                underlay.extend(solid_events(cue, style, name, width, height))
            else:
                events.extend(checklist_events(cue, style, name, width, height))
            continue
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
            f"Style: {name},{style.font},{size:.2f},{ass_color(style.color)},&H0000FFFF,&H00000000,&H80000000,{-1 if style.preset == 'bold' or style.bold else 0},0,0,0,100,100,0,0,{3 if style.background else 1},{style.outline},0,{align},{margin},{margin},{vertical},1"
        )
        if style.packed:
            events.extend(pack_events(cue, style, name, width, height))
        elif style.designed and not (style.preset == "active" and cue.words):
            events.extend(designed_events(cue, style, name, width, height))
        elif style.preset == "active" and cue.words:
            events.extend(active_events(cue, style, name, chars))
        else:
            for j, chunk in enumerate(chunks):
                a = cue.start + (cue.end - cue.start) * j / len(chunks)
                b = cue.start + (cue.end - cue.start) * (j + 1) / len(chunks)
                events.append(
                    f"Dialogue: 0,{stamp(a, True)},{stamp(b, True)},{name},,0,0,0,,{safe_ass(chr(10).join(chunk))}"
                )
    return (
        header.replace("[Events]", "\n".join(styles) + "\n[Events]")
        + "\n".join(underlay + events)
        + "\n"
    )


@functools.lru_cache(maxsize=16)
def _font_file(family, bold):
    try:
        r = subprocess.run(
            ["fc-match", "-f", "%{file}", f"{family}:{'bold' if bold else 'regular'}"],
            capture_output=True,
            text=True,
            env=tool_env(),
            timeout=5,
        )
        return r.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def text_width(text, family, bold, size):
    """Rendered width in px; PIL metrics from the font libass will pick, with a
    conservative per-character fallback when the font file can't be measured."""
    try:
        from PIL import ImageFont

        path = _font_file(family, bold)
        if path:
            return ImageFont.truetype(path, 100).getlength(text) * size / 100
    except (ImportError, OSError):
        pass
    return len(text) * size * 0.62


@functools.lru_cache(maxsize=64)
def font_metrics(family):
    """Vertical metrics libass uses, as fractions of the em: win (ascent+descent,
    which libass treats as the \\fs line height), asc, desc and capital height.
    Read straight from the TTF tables; sane defaults if the font can't be read."""
    default = dict(win=1.0, asc=0.8, desc=0.2, cap=0.7)
    path = _font_file(family, False)
    try:
        with open(path, "rb") as fh:
            data = fh.read(1 << 16)
        count = struct.unpack(">H", data[4:6])[0]
        tables = {}
        for i in range(count):
            tag, _, off, _ = struct.unpack(">4sIII", data[12 + 16 * i : 28 + 16 * i])
            tables[tag] = off
        upm = struct.unpack(">H", data[tables[b"head"] + 18 : tables[b"head"] + 20])[0]
        o = tables[b"OS/2"]
        version = struct.unpack(">H", data[o : o + 2])[0]
        asc, desc = struct.unpack(">HH", data[o + 74 : o + 78])
        cap = struct.unpack(">h", data[o + 88 : o + 90])[0] if version >= 2 else 0
        return dict(
            win=(asc + desc) / upm, asc=asc / upm, desc=desc / upm, cap=(cap / upm) or 0.7
        )
    except (OSError, TypeError, KeyError, struct.error):
        return default


EMPHASIS = re.compile(r"\*([^*\n]+)\*")


def emphasis_segments(line, enabled):
    """Split one line into [(text, highlighted)] using *word* markup."""
    if not enabled:
        return [(line, False)]
    out = []
    pos = 0
    for m in EMPHASIS.finditer(line):
        if m.start() > pos:
            out.append((line[pos : m.start()], False))
        out.append((m.group(1), True))
        pos = m.end()
    if pos < len(line):
        out.append((line[pos:], False))
    return out or [("", False)]


def ass_rgb(s):
    """#RRGGBB -> ASS inline colour &HBBGGRR&."""
    return "&H" + s[5:7] + s[3:5] + s[1:3] + "&"


def designed_events(cue, style, name, width, height):
    """Per-line layout: centred lines at a chosen height, `*word*` emphasis in
    the highlight colour, optional shadow + glow layers behind the text and a
    short fade-in."""
    lines = [
        emphasis_segments(x, bool(style.highlight))
        for x in cue.text.replace("\r", "").split("\n")
    ]
    plain = ["".join(t for t, _ in segs) for segs in lines]
    size = max(12, style.size * height / 1920)
    bold = style.bold or style.preset == "bold"
    widest = max(text_width(t, style.font, bold, size) for t in plain)
    limit = width * style.max_width
    if widest > limit:
        size *= limit / widest
    pitch = size * style.line_spacing
    cy = height * (style.y if style.y is not None else 0.65)
    glow = style.glow * height / 1920
    shade = round(255 * (1 - style.shade))
    fade = rf"\fad({style.fade_in},0)" if style.fade_in else ""
    events = []
    for i, segs in enumerate(lines):
        y = cy + (i - (len(lines) - 1) / 2) * pitch
        head = rf"{{\an5\pos({width / 2:.0f},{y:.0f})\q2\fs{size:.1f}{fade}}}"
        # layer 0: soft dark shadow (legibility on bright/red footage),
        # layer 1: emphasised words' coloured glow, layer 2: crisp text.
        for layer in (0, 1, 2) if glow else (2,):
            body = []
            for text, hot in segs:
                fill = ass_rgb(style.highlight if hot else style.color)
                if layer == 0:
                    body.append(
                        rf"{{\1a&HFF&\3c&H000000&\3a&H{shade:02X}&\bord{glow * 0.6:.1f}\blur{glow * 0.9:.1f}\shad0}}"
                    )
                elif layer == 1:
                    # transparent fill + blurred coloured border = glow
                    alpha = "&H50&" if hot else "&HFF&"
                    body.append(
                        rf"{{\1a&HFF&\3c{ass_rgb(style.highlight)}\3a{alpha}\bord{glow * 0.35:.1f}\blur{glow * 0.6:.1f}\shad0}}"
                    )
                else:
                    body.append(rf"{{\1c{fill}\1a&H00&\bord0\blur0\shad0}}")
                body.append(safe_ass(text))
            events.append(
                f"Dialogue: {layer},{stamp(cue.start, True)},{stamp(cue.end, True)},{name},,0,0,0,,{head}{''.join(body)}"
            )
    return events


# ---------------------------------------------------------------- style pack --

TOKEN = re.compile(r"\*[^*\n]+\*|\s+|[^\s*]+|\*")


def plain_word(w):
    return re.sub(r"[\W_]+", "", w.lower())


def pack_segments(line, style):
    """One text line -> [(text, kind)], kind in n (normal) / hot / key.

    *starred* text is a key word in word mode (or when a key font is set), an
    emphasis colour run when only `highlight` is set, and literal otherwise."""
    star = "key" if (style.word_mode == "word" or style.key_font) else (
        "hot" if style.highlight else None
    )
    keys = {plain_word(k) for k in style.keywords}
    out = []
    for tok in TOKEN.findall(line):
        if len(tok) > 2 and tok[0] == tok[-1] == "*" and star:
            kind, text = star, tok[1:-1]
        else:
            kind, text = "n", tok
            if keys and not tok.isspace() and plain_word(tok) in keys:
                kind = "key"
        if style.case == "upper":
            text = text.upper()
        elif style.case == "lower":
            text = text.lower()
        if out and out[-1][1] == kind:
            out[-1] = (out[-1][0] + text, kind)
        else:
            out.append((text, kind))
    return out or [("", "n")]


def seg_font(style, kind):
    if kind == "key" and style.key_font:
        return style.key_font, style.key_scale
    return style.font, 1.0


def seg_colour(style, kind):
    if kind == "hot":
        return style.highlight or style.color
    if kind == "key":
        return style.key_color or style.highlight or style.color
    return style.color


def blend(colors, t):
    """Piecewise-linear blend across hex colours at t in [0, 1] -> '#RRGGBB'."""
    if len(colors) == 1:
        return colors[0]
    t = min(1, max(0, t)) * (len(colors) - 1)
    i = min(int(t), len(colors) - 2)
    f = t - i
    a, b = colors[i], colors[i + 1]
    ch = [
        round(int(a[k : k + 2], 16) * (1 - f) + int(b[k : k + 2], 16) * f)
        for k in (1, 3, 5)
    ]
    return "#{:02X}{:02X}{:02X}".format(*ch)


def word_units(cue, style):
    """[(text, start, end)] for word mode: real word timings when the cue has
    them, otherwise an even split of the cue text (manual cue lists)."""
    words = sorted((w for w in cue.words if w.end > w.start), key=lambda w: w.start)
    if not words:
        toks = cue.text.split()
        weights = [max(1, len(t.strip("*"))) for t in toks]
        total = sum(weights) or 1
        t, words = cue.start, []
        for tok, wt in zip(toks, weights):
            d = (cue.end - cue.start) * wt / total
            words.append(Word(start=t, end=t + d, text=tok))
            t += d
    n = style.words_per
    groups = [words[i : i + n] for i in range(0, len(words), n)]
    units = []
    for k, g in enumerate(groups):
        a = max(cue.start, g[0].start) if k else min(g[0].start, cue.end - 0.05)
        nxt = groups[k + 1][0].start if k + 1 < len(groups) else cue.end
        b = min(cue.end, nxt, g[-1].end + 0.4)
        b = max(b, min(cue.end, a + 0.08))
        if b > a:
            units.append((" ".join(w.text.strip() for w in g), a, b))
    return units


def entrance_tags(style, x, y, height, dur_ms):
    """(extra override tags, position tag) for the entrance/exit of one event."""
    ms = min(style.entrance_ms, max(40, dur_ms // 2))
    e = style.entrance
    fade_in = style.fade_in
    pos = rf"\pos({x:.0f},{y:.0f})"
    extra = ""
    if e in ("fade", "blur", "rise", "blur-rise", "pop", "split"):
        fade_in = max(fade_in, ms)
    if e in ("blur", "blur-rise", "split"):
        b = max(2, 14 * height / 1920)
        extra += rf"\blur{b:.1f}\t(0,{ms},\blur0)"
        if style.exit_ms:
            extra += rf"\t({max(0, dur_ms - style.exit_ms)},{dur_ms},\blur{b:.1f})"
    if e in ("rise", "blur-rise"):
        dy = 0.03 * height
        pos = rf"\move({x:.0f},{y + dy:.0f},{x:.0f},{y:.0f},0,{ms})"
    if e == "pop":
        extra += rf"\fscx62\fscy62\t(0,{ms},\fscx100\fscy100)"
    if fade_in or style.exit_ms:
        extra += rf"\fad({fade_in},{style.exit_ms})"
    return pos + extra


def pack_events(cue, style, name, width, height):
    """Style-pack layout: per-line centred text with optional letter spacing, case,
    key-word fonts, per-character gradient, hollow lettering, soft shadow/glow,
    entrance animation (fade/blur/rise/pop/RGB-split) and word-by-word timing."""
    if style.word_mode == "word":
        parts = word_units(cue, style)
    else:
        parts = [(cue.text, cue.start, cue.end)]
    events = []
    for text, a, b in parts:
        events.extend(pack_unit(text, a, b, style, name, width, height))
    return events


def pack_unit(text, start, end, style, name, width, height):
    lines = [pack_segments(x, style) for x in text.replace("\r", "").split("\n")]
    base = max(12, style.size * height / 1920)
    spacing = style.spacing * height / 1920
    bold = style.bold or style.preset == "bold"

    def line_width(segs, size):
        total = 0.0
        for t, kind in segs:
            fam, scale = seg_font(style, kind)
            total += text_width(t, fam, bold, size * scale)
        return total + spacing * size / base * len(
            "".join(t for t, _ in segs)
        )

    widest = max(line_width(s, base) for s in lines)
    limit = width * style.max_width
    size = base * (limit / widest) if widest > limit else base
    k = size / base
    pitch = size * style.line_spacing
    cx = width * (style.x if style.x is not None else 0.5)
    cy = height * (style.y if style.y is not None else 0.65)
    dur_ms = max(1, round((end - start) * 1000))
    glow = style.glow * height / 1920
    shade = round(255 * (1 - style.shade))
    border = style.outline * height / 1920 * k
    stamps = f"{stamp(start, True)},{stamp(end, True)}"
    events = []
    fm = font_metrics(style.font)
    for i, segs in enumerate(lines):
        y = cy + (i - (len(lines) - 1) / 2) * pitch
        # \\an5 centres the line box; shift so the middle of the capitals sits on y.
        y -= ((fm["asc"] - fm["desc"]) / 2 - fm["cap"] / 2) * size
        head = rf"{{\an5\q2\fsp{spacing * k:.2f}{entrance_tags(style, cx, y, height, dur_ms)}}}"
        n_chars = sum(1 for t, _ in segs for ch in t if not ch.isspace())

        def body(layer):
            out, idx = [], 0
            for t, kind in segs:
                fam, scale = seg_font(style, kind)
                fs = rf"\fn{fam}\fs{size * scale * font_metrics(fam)['win']:.1f}"
                hot = kind != "n" and (style.highlight or style.key_color)
                if layer == "shadow":
                    out.append(
                        rf"{{{fs}\1a&HFF&\3c&H000000&\3a&H{shade:02X}&\bord{glow * 0.6:.1f}\blur{glow * 0.9:.1f}\shad0}}{safe_ass(t)}"
                    )
                elif layer == "halo":
                    hc = style.highlight or style.key_color
                    alpha = "&H50&" if hot else "&HFF&"
                    out.append(
                        rf"{{{fs}\1a&HFF&\3c{ass_rgb(hc)}\3a{alpha}\bord{glow * 0.35:.1f}\blur{glow * 0.6:.1f}\shad0}}{safe_ass(t)}"
                    )
                elif layer in ("split_a", "split_b"):
                    colour = (style.accent or "#FF2D55") if layer == "split_a" else "#00E5FF"
                    out.append(
                        rf"{{{fs}\1c{ass_rgb(colour)}\1a&H70&\bord0\shad0}}{safe_ass(t)}"
                    )
                else:
                    stroke = (
                        rf"\bord{border:.1f}\3c{ass_rgb(style.color if style.hollow else style.outline_color)}\3a&H00&"
                        if border
                        else r"\bord0"
                    )
                    fill_a = r"\1a&HFF&" if style.hollow else r"\1a&H00&"
                    if style.gradient:
                        for ch in t:
                            if ch.isspace():
                                out.append(ch)
                                continue
                            c = blend(style.gradient, idx / max(1, n_chars - 1))
                            idx += 1
                            out.append(
                                rf"{{{fs}\1c{ass_rgb(c)}{fill_a}{stroke}\shad0}}{safe_ass(ch)}"
                            )
                    else:
                        out.append(
                            rf"{{{fs}\1c{ass_rgb(seg_colour(style, kind))}{fill_a}{stroke}\shad0}}{safe_ass(t)}"
                        )
            return "".join(out)

        layers = []
        if glow:
            layers.append((1, "shadow"))
            if style.highlight or style.key_color:
                layers.append((2, "halo"))
        if style.entrance == "split":
            layers += [(3, "split_a"), (4, "split_b")]
        layers.append((5, "main"))
        for layer, kind in layers:
            h = head
            span = stamps
            if kind in ("split_a", "split_b"):
                d = 0.02 * width * (-1 if kind == "split_a" else 1)
                ms = min(style.entrance_ms, max(40, dur_ms // 2))
                span = f"{stamp(start, True)},{stamp(min(end, start + ms * 2.4 / 1000), True)}"
                h = rf"{{\an5\q2\fsp{spacing * k:.2f}\move({cx - d:.0f},{y:.0f},{cx:.0f},{y:.0f},0,{ms})\blur2\fad(0,140)}}"
            events.append(
                f"Dialogue: {layer},{span},{name},,0,0,0,,{h}{body(kind)}"
            )
    return events


def rrect(x0, y0, x1, y1, r):
    """ASS drawing path for a rounded rectangle."""
    r = max(0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
    k = r * 0.5523

    def f(*v):
        return " ".join(f"{a:.1f}" for a in v)

    return (
        f"m {f(x0 + r, y0)} l {f(x1 - r, y0)} b {f(x1 - r + k, y0, x1, y0 + r - k, x1, y0 + r)} "
        f"l {f(x1, y1 - r)} b {f(x1, y1 - r + k, x1 - r + k, y1, x1 - r, y1)} "
        f"l {f(x0 + r, y1)} b {f(x0 + r - k, y1, x0, y1 - r + k, x0, y1 - r)} "
        f"l {f(x0, y0 + r)} b {f(x0, y0 + r - k, x0 + r - k, y0, x0 + r, y0)}"
    )


def circle_path(cx, cy, r):
    return rrect(cx - r, cy - r, cx + r, cy + r, r)


def tick_path(cx, cy, r, thick=0.22):
    """Thick check mark as a filled polygon (open strokes would not render)."""
    pts = [(-0.46, 0.04), (-0.1, 0.4), (0.52, -0.42)]
    pts = [(cx + px * r, cy + py * r) for px, py in pts]
    t = thick * r / 2
    left, right = [], []
    for i, (px, py) in enumerate(pts):
        if i == 0:
            dx, dy = pts[1][0] - px, pts[1][1] - py
        elif i == len(pts) - 1:
            dx, dy = px - pts[i - 1][0], py - pts[i - 1][1]
        else:
            dx, dy = pts[i + 1][0] - pts[i - 1][0], pts[i + 1][1] - pts[i - 1][1]
        n = (dx * dx + dy * dy) ** 0.5 or 1
        nx, ny = -dy / n, dx / n
        left.append((px + nx * t, py + ny * t))
        right.append((px - nx * t, py - ny * t))
    poly = left + right[::-1]
    return "m " + " l ".join(f"{x:.1f} {y:.1f}" for x, y in poly)


def draw(path, colour, alpha="&H00&", layer=1, extra="", span="", name="", fade=""):
    return (
        f"Dialogue: {layer},{span},{name},,0,0,0,,"
        rf"{{\an7\pos(0,0)\1c{ass_rgb(colour)}\1a{alpha}\bord0\shad0{extra}{fade}\p1}}{path}{{\p0}}"
    )


def solid_events(cue, style, name, width, height):
    fade = rf"\fad({style.fade_in},{style.exit_ms})" if style.fade_in or style.exit_ms else ""
    span = f"{stamp(cue.start, True)},{stamp(cue.end, True)}"
    return [
        draw(f"m 0 0 l {width} 0 l {width} {height} l 0 {height}", cue.fill,
             layer=0, span=span, name=name, fade=fade)
    ]


def checklist_events(cue, style, name, width, height):
    """Rounded card with one row per item; row k gets a filled tick circle at
    cue.start + check_at[k] (default stagger 0.8s + 0.55s per row)."""
    items = cue.items
    n = len(items)
    fam = style.font
    cw = width * max(0.4, min(0.95, style.max_width))
    fs = style.size * height / 1920
    longest = max(text_width(t, fam, False, 1.0) for t in items)
    fs = min(fs, cw / (longest + 3.45))
    pitch, r, pad = fs * 1.65, fs * 0.55, fs * 0.9
    ch = 2 * pad + (n - 1) * pitch + 2 * r
    x0 = width * (style.x if style.x is not None else 0.5) - cw / 2
    y0 = height * (style.y if style.y is not None else 0.5) - ch / 2
    x1, y1 = x0 + cw, y0 + ch
    tx = x0 + pad + 2 * r + fs * 0.55
    accent = style.accent or "#D4AF37"
    times = [cue.start + t for t in cue.check_at]
    times += [cue.start + 0.8 + 0.55 * k for k in range(len(times), n)]
    times = [min(cue.end, t) for t in times[:n]]
    bounds = sorted({cue.start, cue.end, *times})
    events = []
    for j, (a, b) in enumerate(zip(bounds, bounds[1:])):
        if b <= a:
            continue
        done = sum(1 for t in times if t <= a + 1e-6)
        span = f"{stamp(a, True)},{stamp(b, True)}"
        first, last = j == 0, b >= cue.end - 1e-6
        fade = rf"\fad({style.entrance_ms if first else 0},{style.exit_ms if last else 0})"
        events.append(
            draw(rrect(x0, y0 + fs * 0.28, x1, y1 + fs * 0.28, fs * 0.9), "#000000",
                 alpha="&HB0&", layer=1, extra=rf"\blur{fs * 0.55:.1f}", span=span,
                 name=name, fade=fade)
        )
        events.append(
            draw(rrect(x0, y0, x1, y1, fs * 0.9), cue.fill or "#FAF8F2", layer=2,
                 span=span, name=name, fade=fade)
        )
        for k, text in enumerate(items):
            cy = y0 + pad + r + k * pitch
            cx = x0 + pad + r
            if k < done:
                events.append(draw(circle_path(cx, cy, r), accent, layer=3, span=span,
                                   name=name, fade=fade))
                events.append(draw(tick_path(cx, cy, r), "#FFFFFF", layer=4, span=span,
                                   name=name, fade=fade))
            else:
                events.append(
                    draw(circle_path(cx, cy, r - fs * 0.04), "#BDBDBD", alpha="&HFF&",
                         layer=3, extra=rf"\3c{ass_rgb('#BDBDBD')}\bord{fs * 0.08:.1f}",
                         span=span, name=name, fade=fade)
                )
            fm = font_metrics(fam)
            ty = cy - ((fm["asc"] - fm["desc"]) / 2 - fm["cap"] / 2) * fs
            events.append(
                f"Dialogue: 4,{span},{name},,0,0,0,,{{\\an4\\pos({tx:.0f},{ty:.0f})\\fn{fam}\\fs{fs * fm['win']:.1f}\\1c{ass_rgb(style.color)}\\bord0\\shad0{fade}}}{safe_ass(text)}"
            )
    return events


def active_events(cue, style, name, chars):
    """Keep a readable phrase visible while coloring only the currently spoken word."""
    words = sorted(
        (w for w in cue.words if w.end > cue.start and w.start < cue.end),
        key=lambda w: w.start,
    )
    groups = []
    current = []
    for word in words:
        proposed = " ".join(w.text.strip() for w in [*current, word])
        if current and len(textwrap.wrap(proposed, width=chars)) > style.max_lines:
            groups.append(current)
            current = []
        current.append(word)
    if current:
        groups.append(current)
    events = []
    for i, group in enumerate(groups):
        start = cue.start if i == 0 else max(cue.start, group[0].start)
        end = min(cue.end, groups[i + 1][0].start) if i + 1 < len(groups) else cue.end
        boundaries = sorted(
            {
                start,
                end,
                *(max(start, min(end, t)) for w in group for t in (w.start, w.end)),
            }
        )
        # Wrap plain characters first so ASS tags don't distort measured line lengths.
        plain = " ".join(w.text.strip() for w in group)
        wrapped = "\n".join(textwrap.wrap(plain, width=chars))
        spans = []
        cursor = 0
        for w in group:
            spans.append((cursor, cursor + len("".join(w.text.split())), w))
            cursor += len("".join(w.text.split()))
        for a, b in zip(boundaries, boundaries[1:]):
            if b <= a:
                continue
            middle = (a + b) / 2
            body = []
            pos = 0
            previous = False
            for ch in wrapped:
                active = not ch.isspace() and any(
                    left <= pos < right and w.start <= middle < w.end
                    for left, right, w in spans
                )
                if active != previous:
                    body.append(
                        r"{\c&H00FFFF&}"
                        if active
                        else "{\\c" + ass_color(style.color) + "&}"
                    )
                    previous = active
                body.append(safe_ass(ch))
                if not ch.isspace():
                    pos += 1
            events.append(
                f"Dialogue: 0,{stamp(a, True)},{stamp(b, True)},{name},,0,0,0,,{''.join(body)}"
            )
    return events


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
