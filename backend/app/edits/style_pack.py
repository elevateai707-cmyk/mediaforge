"""Caption/graphics style pack: named Style presets and multi-element recipes.

Presets are plain dicts of `Style` fields; `Style(preset="serif-card")` fills them
in under whatever the plan sets explicitly. Recipes expand one request into the
overlay dicts (timeline_overlays entries) that build a multi-element graphic -
chapter cards, mixed-type titles, word cards, checklists, insets.

Everything is original technique work inspired by short-form "designed text"
conventions; nothing here reproduces a particular creator's layout or copy.
Colours default to the Build With DB brand (gold on black/cream) and are
parameters on every recipe.
"""

from __future__ import annotations

# Free / OFL families installed by scripts/install_style_fonts.py. Each instance
# is its own family so libass picks it by name without bold/italic flags.
FONTS = (
    "DejaVu Sans",
    "Liberation Sans",
    "Montserrat Light",
    "Montserrat Medium",
    "Montserrat SemiBold",
    "Montserrat Bold",
    "Montserrat ExtraBold",
    "Montserrat Black",
    "Montserrat Light Italic",
    "Montserrat Bold Italic",
    "Montserrat ExtraBold Italic",
    "Montserrat Black Italic",
    "Cormorant Garamond Medium",
    "Cormorant Garamond SemiBold",
    "Cormorant Garamond Bold",
    "Cinzel SemiBold",
    "Playfair Display SemiBold",
    "Allura",
    "Great Vibes",
    "Parisienne",
    "Anton",
    "Archivo Black",
)

# Brand palette (Build With DB). Recipes take these as defaults.
GOLD = "#D4AF37"
BLACK = "#0B0B0B"
CREAM = "#F3EEE6"
CARD_CREAM = "#FAF5EA"  # flat word-card background
INK = "#2A1D10"
HOOK_YELLOW = "#FFE600"

PRESETS: dict[str, dict] = {
    "kinetic-db": dict(
        font="Anton", size=84, color=GOLD, key_color=CREAM, key_font="Anton",
        key_scale=1.62, case="upper", outline=0, background=False, glow=18,
        shade=0.7, y=0.72, position="center", max_width=0.82,
        word_mode="word", words_per=1, entrance="pop", entrance_ms=90, exit_ms=30,
    ),
    "kinetic-teal": dict(
        font="Anton", size=84, color="#48D6CB", key_color="#FFFFFF", key_font="Anton",
        key_scale=1.62, case="upper", outline=0, background=False, glow=18,
        shade=0.7, y=0.72, position="center", max_width=0.82,
        word_mode="word", words_per=1, entrance="pop", entrance_ms=90, exit_ms=30,
    ),
    # (a) one word at a time, hard cut on the beat; key words go heavy italic.
    "kinetic-word": dict(
        font="Montserrat Light", size=62, color="#FFFFFF", outline=0, glow=14,
        shade=0.7, y=0.61, max_width=0.8, word_mode="word", words_per=1,
        key_font="Montserrat ExtraBold Italic", key_scale=1.13,
    ),
    # (b) the heavy half of a mixed-type title; the script half is "script-accent".
    "title-heavy": dict(
        font="Montserrat Black Italic", size=210, color=GOLD, outline=0, glow=22,
        shade=0.7, y=0.5, max_width=0.86, entrance="blur-rise", entrance_ms=300,
        exit_ms=200,
    ),
    "script-accent": dict(
        font="Great Vibes", size=150, color="#FFFFFF", outline=0, glow=14,
        shade=0.55, y=0.56, max_width=0.7, entrance="blur", entrance_ms=320,
        exit_ms=200,
    ),
    # (c)/(e) serif capitals: on a flat card, or over footage.
    "serif-card": dict(
        font="Cormorant Garamond Bold", size=112, color=INK, outline=0, y=0.5,
        spacing=9, case="upper", max_width=0.86,
    ),
    "serif-caption": dict(
        font="Cormorant Garamond SemiBold", size=84, color=CREAM, outline=0,
        glow=12, shade=0.5, y=0.7, spacing=8, case="upper", max_width=0.86,
        entrance="fade", entrance_ms=90,
    ),
    # (d) chapter card: script heading + small serif caps beneath.
    "chapter-script": dict(
        font="Allura", size=128, color=CREAM, outline=0, y=0.49, max_width=0.8,
        entrance="fade", entrance_ms=350, exit_ms=250,
    ),
    "chapter-sub": dict(
        font="Cormorant Garamond SemiBold", size=46, color=CREAM, outline=0,
        y=0.545, spacing=7, case="upper", max_width=0.8, entrance="fade",
        entrance_ms=450, exit_ms=250,
    ),
    # (f) pinned title for the whole video.
    "title-bar": dict(
        font="Montserrat Medium", size=66, color="#FFFFFF", outline=0, glow=10,
        shade=0.55, y=0.13, spacing=2, case="upper", max_width=0.8,
        line_spacing=1.15,
    ),
    # (g) heavy gradient word with an RGB-split entrance.
    "gradient-word": dict(
        font="Montserrat ExtraBold", size=140, color="#FFFFFF", outline=0,
        glow=18, shade=0.42, y=0.5, max_width=0.86,
        gradient=["#F7E08A", GOLD, "#B8860B"], accent="#FF2D55",
        entrance="split", entrance_ms=300, exit_ms=160,
    ),
    # (h) two-tone headline: hollow caps + heavy italic accent line.
    "headline-hollow": dict(
        font="Montserrat Medium", size=92, color="#FFFFFF", outline=3, hollow=True,
        glow=16, shade=0.5, y=0.6, spacing=3, case="upper", max_width=0.86,
        entrance="blur", entrance_ms=260,
    ),
    "headline-accent": dict(
        font="Montserrat ExtraBold Italic", size=124, color=GOLD, outline=0,
        glow=22, shade=0.7, y=0.66, case="lower", max_width=0.86,
        entrance="blur-rise", entrance_ms=300,
    ),
    # (k) tiny serif label ("THE RESULT") pinned near the top.
    "label-caps": dict(
        font="Cormorant Garamond SemiBold", size=44, color=CREAM, outline=0,
        glow=8, shade=0.4, y=0.17, spacing=8, case="upper", max_width=0.7,
        entrance="fade", entrance_ms=300,
    ),
    # Hook line: bold white lettering, dark outline, variable part highlighted.
    "hook-caption": dict(
        font="Montserrat SemiBold", size=84, color="#FFFFFF", outline=4,
        highlight=HOOK_YELLOW, glow=0, y=0.3, max_width=0.88, line_spacing=1.1,
        entrance="pop", entrance_ms=140,
    ),
    # (i) row text inside a checklist card (the card itself is Overlay.kind).
    "checklist-text": dict(
        font="Montserrat Medium", size=46, color="#222222", outline=0, y=0.5,
        max_width=0.82,
    ),
}


def _o(start, end, text="", **extra):
    return dict(start=start, end=end, text=text, words=[], **extra)


def _style(preset, **over):
    return dict(preset=preset, **over)


def mixed_title(start, end, heavy, script, accent=GOLD, ink="#FFFFFF", y=0.5):
    """Heavy italic word with a script word overlapping its lower-right."""
    return [
        _o(start, end, heavy, style=_style("title-heavy", color=accent, y=y)),
        _o(start + 0.45, end, script,
           style=_style("script-accent", color=ink, y=y + 0.07, x=0.58)),
    ]


def chapter_card(start, end, title, subtitle="", bg=BLACK, ink=CREAM, accent=GOLD):
    out = [_o(start, end, kind="solid", fill=bg, style=_style("clean", fade_in=0))]
    out.append(_o(start + 0.05, end, title,
                  style=_style("chapter-script", color=accent)))
    if subtitle:
        out.append(_o(start + 0.35, end, subtitle,
                      style=_style("chapter-sub", color=ink)))
    return out


def word_cards(words, bg=CARD_CREAM, ink=INK, accent=None):
    """words: [(text, start, end)] - one centred serif word per beat on a flat card."""
    start, end = words[0][1], words[-1][2]
    out = [_o(start, end, kind="solid", fill=bg, style=_style("clean"))]
    for text, a, b in words:
        out.append(_o(a, b, text, style=_style("serif-card", color=ink)))
    return out


def two_tone_headline(start, end, top, bottom, accent=GOLD, ink="#FFFFFF", y=0.6):
    return [
        _o(start, end, top, style=_style("headline-hollow", color=ink, y=y)),
        _o(start + 0.3, end, bottom,
           style=_style("headline-accent", color=accent, y=y + 0.075)),
    ]


def result_label(start, end, text="the result", ink=CREAM):
    return [_o(start, end, text, style=_style("label-caps", color=ink))]


def gradient_word(start, end, text, colors=None, accent="#FF2D55", y=0.5):
    kw = dict(y=y, accent=accent)
    if colors:
        kw["gradient"] = list(colors)
    return [_o(start, end, text, style=_style("gradient-word", **kw))]


def title_bar(start, end, text, ink="#FFFFFF", y=0.13):
    return [_o(start, end, text, style=_style("title-bar", color=ink, y=y))]


def checklist(start, end, items, check_at=None, fill="#FAF8F2", accent=GOLD,
              ink="#222222", y=0.5):
    return [
        _o(start, end, kind="checklist", items=list(items),
           check_at=list(check_at) if check_at is not None
           else [0.8 + 0.55 * k for k in range(len(items))],
           fill=fill,
           style=_style("checklist-text", color=ink, accent=accent, y=y,
                        entrance_ms=220, exit_ms=180))
    ]


def inset(start, end, media, x=0.05, y=0.05, w=0.42, h=0.22, radius=0.1,
          gray=False, anim="fade", src_start=0.0, border=None, shadow=True,
          anim_ms=250):
    return [
        _o(start, end, kind="inset", media=str(media),
           inset=dict(x=x, y=y, w=w, h=h, radius=radius, gray=gray, anim=anim,
                      src_start=src_start, border=border, shadow=shadow,
                      anim_ms=anim_ms))
    ]


def split_words(text, start, end):
    """Manual cue list helper: even split of `text` over [start, end], weighted by
    word length, returned as Word dicts for Overlay/Cue.words."""
    words = text.split()
    if not words:
        return []
    weights = [max(1, len(w.strip("*"))) for w in words]
    total = sum(weights)
    out, t = [], start
    for w, wt in zip(words, weights):
        d = (end - start) * wt / total
        out.append(dict(start=round(t, 4), end=round(t + d, 4), text=w))
        t += d
    return out


RECIPES = {
    "mixed-title": mixed_title,
    "chapter-card": chapter_card,
    "word-cards": word_cards,
    "two-tone-headline": two_tone_headline,
    "result-label": result_label,
    "gradient-word": gradient_word,
    "title-bar": title_bar,
    "checklist": checklist,
    "inset": inset,
}
