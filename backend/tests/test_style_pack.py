"""Style pack: named presets, recipes, word-by-word timing, checklist/solid/inset
overlays, B&W reveal and the hook library."""

import re
import subprocess

import numpy as np
import pytest
from app import config
from app.edits import hooks, style_pack
from app.edits.project import Clip, Cue, Inset, Overlay, Project, Style, Word
from app.edits.render_v2 import look_filter, render_snapshot
from app.edits.subtitles import (
    ass_document,
    blend,
    font_metrics,
    pack_segments,
    word_units,
)
from app.jobs import _CancelFlag
from app.proc import tool_env

from .test_editor_v2 import fixture_video, frame


def project_with(overlays, **kw):
    c = Clip(asset_id=1, start=0, end=6)
    return Project(clips=[c], on_video_text=True, timeline_overlays=overlays, **kw)


def lines(doc, layer=None):
    out = [x for x in doc.splitlines() if x.startswith("Dialogue:")]
    if layer is not None:
        out = [x for x in out if x.startswith(f"Dialogue: {layer},")]
    return out


def doc_for(overlays, **kw):
    return ass_document(project_with(overlays, **kw), 1080, 1920, speech=False)


def ov(text, style, start=0.0, end=2.0, **kw):
    return Overlay(start=start, end=end, text=text, style=style, **kw)


# ------------------------------------------------------------------ presets --


def test_presets_fill_fields_and_explicit_values_win():
    s = Style(preset="serif-card")
    assert s.packed and s.case == "upper" and s.font == "Cormorant Garamond Bold"
    assert Style(preset="serif-card", case="none", size=60).case == "none"
    assert Style(preset="serif-card", size=60).size == 60
    # Round-trips: dumping and re-validating keeps the same values.
    assert Style.model_validate(s.model_dump()) == s
    # Legacy presets and defaults are untouched.
    for legacy in ("clean", "bold", "active"):
        assert not Style(preset=legacy).packed
    assert not Style().packed
    assert Style(spacing=3).packed and Style(entrance="blur").packed


def test_every_preset_validates_and_uses_a_known_font():
    for name, values in style_pack.PRESETS.items():
        s = Style(preset=name)
        assert s.font in style_pack.FONTS, name
        assert s.packed
        for k, v in values.items():
            assert getattr(s, k) == v, (name, k)


def test_style_validation_rejects_bad_values():
    for bad in (
        dict(gradient=["#FFFFFF"]),
        dict(gradient=["#FFFFFF", "red"]),
        dict(accent="gold"),
        dict(entrance="spin"),
        dict(font="Comic Sans"),
        dict(words_per=0),
        dict(key_scale=5),
    ):
        with pytest.raises(ValueError):
            Style(**bad)


def test_overlay_kinds_validate():
    with pytest.raises(ValueError):
        Overlay(start=0, end=1, text="", kind="solid")
    with pytest.raises(ValueError):
        Overlay(start=0, end=1, text="", kind="checklist")
    with pytest.raises(ValueError):
        Overlay(start=0, end=1, text="", kind="inset", media="/x.mp4")
    c = Clip(asset_id=1, end=2)
    bad = Overlay(start=0, end=1, text="", kind="solid", fill="#000000")
    with pytest.raises(ValueError):  # cards belong on the final timeline
        Project(clips=[c], overlays={c.uid: [bad]})
    Project(clips=[c], timeline_overlays=[bad])


# ------------------------------------------------------ word-by-word captions --


def test_word_mode_uses_real_word_timings_and_holds_briefly():
    words = [
        Word(start=0.0, end=0.3, text="because"),
        Word(start=0.4, end=0.7, text="we"),
        Word(start=1.6, end=1.9, text="teleprompter"),
    ]
    cue = Cue(start=0, end=2.0, text="because we teleprompter", words=words)
    style = Style(preset="kinetic-word")
    units = word_units(cue, style)
    assert [u[0] for u in units] == ["because", "we", "teleprompter"]
    assert units[0][1:] == (0.0, 0.4)  # holds until the next word starts
    assert units[1][2] == pytest.approx(1.1)  # long gap: hold capped at +0.4 s
    assert units[2][1] == 1.6


def test_word_mode_even_split_for_manual_cue_lists():
    cue = Cue(start=1.0, end=3.0, text="stop wasting time now")
    units = word_units(cue, Style(preset="kinetic-word"))
    assert [u[0] for u in units] == ["stop", "wasting", "time", "now"]
    assert units[0][1] == 1.0 and units[-1][2] == pytest.approx(3.0)
    starts = [u[1] for u in units]
    assert starts == sorted(starts)
    grouped = word_units(cue, Style(preset="kinetic-word", words_per=2))
    assert [u[0] for u in grouped] == ["stop wasting", "time now"]


def test_kinetic_key_words_switch_font_and_size():
    style = Style(preset="kinetic-word", keywords=["Teleprompter."])
    segs = pack_segments("use a teleprompter, now", style)
    assert ("teleprompter,", "key") in segs
    starred = pack_segments("use *scripts* daily", Style(preset="kinetic-word"))
    assert ("scripts", "key") in starred
    # Without a key font or highlight, asterisks stay literal.
    assert pack_segments("a *b*", Style(spacing=2)) == [("a *b*", "n")]

    words = [
        Word(start=0, end=0.4, text="every"),
        Word(start=0.4, end=0.9, text="*actor*"),
    ]
    cue = Cue(start=0, end=1.0, text="every *actor*", words=words)
    doc = doc_for([Overlay(start=0, end=1.0, text=cue.text, words=words,
                           style=Style(preset="kinetic-word"))])
    main = lines(doc, 5)
    assert len(main) == 2
    assert r"\fnMontserrat Light" in main[0] and "every" in main[0]
    assert r"\fnMontserrat ExtraBold Italic" in main[1] and "actor" in main[1]
    assert "0:00:00.00,0:00:00.40" in main[0] and "0:00:00.40,0:00:01.00" in main[1]
    # Key word is drawn larger than the regular word.
    fs = [float(re.search(r"\\fs([\d.]+)", m).group(1)) for m in main]
    assert fs[1] > fs[0]


def test_kinetic_captions_from_narration_words_through_speech_captions():
    c = Clip(asset_id=1, start=0, end=4)
    words = [Word(start=0.2, end=0.5, text="hello"), Word(start=0.6, end=1.0, text="world")]
    p = Project(
        clips=[c],
        speech_captions=True,
        caption_source="narration",
        narration_captions=[Cue(start=0.2, end=1.0, text="hello world", words=words)],
        caption_style=Style(preset="kinetic-word"),
    )
    p.voice_over.start = 1.0
    d = lines(ass_document(p, 1080, 1920), 5)
    assert len(d) == 2 and "0:00:01.20" in d[0] and "0:00:01.60" in d[1]


# --------------------------------------------------------------- text presets --


def test_serif_card_is_uppercase_letterspaced_and_centred_on_cap_height():
    d = lines(doc_for([ov("stop", Style(preset="serif-card"))]), 5)[0]
    assert "STOP" in d and "stop" not in d
    assert r"\fnCormorant Garamond Bold" in d
    assert re.search(r"\\fsp(\d+\.\d+)", d) and float(re.search(r"\\fsp([\d.]+)", d).group(1)) > 5
    assert r"\pos(540," in d
    fm = font_metrics("Cormorant Garamond Bold")
    if fm["win"] != 1.0:  # font installed: libass line height is the win metrics
        fs = float(re.search(r"\\fs([\d.]+)", d).group(1))
        assert fs == pytest.approx(112 * fm["win"], rel=0.02)


def test_fs_is_em_size_via_win_metrics():
    fm = font_metrics("Montserrat Light")
    if fm["win"] == 1.0:
        pytest.skip("style fonts not installed")
    assert fm["win"] == pytest.approx(1.562, abs=0.01)
    d = lines(doc_for([ov("hi", Style(preset="kinetic-word"))]), 5)[0]
    fs = float(re.search(r"\\fs([\d.]+)", d).group(1))
    assert fs == pytest.approx(62 * 1.562, rel=0.01)


def test_title_bar_two_lines_pinned_at_height():
    t = style_pack.title_bar(0, 3, "first line\nsecond line", y=0.12)[0]
    d = lines(doc_for([Overlay.model_validate(t)]), 5)
    assert len(d) == 2
    assert "FIRST LINE" in d[0] and "SECOND LINE" in d[1]
    ys = [int(re.search(r"\\pos\(540,(\d+)\)", x).group(1)) for x in d]
    assert ys[0] < ys[1] and abs((ys[0] + ys[1]) / 2 - 0.12 * 1920) < 8


def test_gradient_blends_per_character_and_split_entrance_layers():
    assert blend(["#000000", "#FFFFFF"], 0.5) == "#808080"
    assert blend(["#FF0000", "#00FF00", "#0000FF"], 1) == "#0000FF"
    o = Overlay.model_validate(
        style_pack.gradient_word(0, 2, "abc", colors=["#FF0000", "#0000FF"])[0]
    )
    doc = doc_for([o])
    main = lines(doc, 5)[0]
    cols = re.findall(r"\\1c&H([0-9A-F]{6})&", main)
    assert cols[0] == "0000FF" and cols[-1] == "FF0000"  # BGR of red / blue
    assert len(cols) == 3
    # RGB-split entrance: accent + cyan copies offset left/right with \move.
    a, b = lines(doc, 3)[0], lines(doc, 4)[0]
    assert r"\move(" in a and r"\move(" in b and "&HFF2D55" not in a
    assert "0:00:00.00,0:00:00.00" not in a
    assert r"\blur" in main and r"\t(0,300,\blur0)" in main


def test_hollow_headline_uses_transparent_fill_and_stroke():
    top, bottom = style_pack.two_tone_headline(0, 3, "keep going", "start now")
    d = lines(doc_for([Overlay.model_validate(top), Overlay.model_validate(bottom)]), 5)
    assert r"\1a&HFF&" in d[0] and r"\bord" in d[0] and "KEEP GOING" in d[0]
    assert "start now" in d[1] and r"\1a&H00&" in d[1]  # lower-case accent line
    assert top["start"] == 0 and bottom["start"] == pytest.approx(0.3)


def test_mixed_title_recipe_heavy_plus_delayed_script_with_entrances():
    heavy, script = style_pack.mixed_title(1.0, 4.0, "Calgary", "summer", accent="#D4AF37")
    assert heavy["style"]["preset"] == "title-heavy" and script["style"]["preset"] == "script-accent"
    assert script["start"] == pytest.approx(1.45) and script["style"]["y"] > heavy["style"]["y"]
    doc = doc_for([Overlay.model_validate(heavy), Overlay.model_validate(script)])
    h = [x for x in lines(doc, 5) if "Calgary" in x][0]
    s = [x for x in lines(doc, 5) if "summer" in x][0]
    assert r"\fnMontserrat Black Italic" in h and r"\move(" in h and r"\blur" in h
    assert r"\fnGreat Vibes" in s and r"\pos(626," in s  # x=0.58 of 1080
    assert "&H37AFD4&" in h  # gold #D4AF37 (BGR)


def test_label_caps_and_entrance_variants():
    d = lines(doc_for([ov("the result", Style(preset="label-caps"))]), 5)[0]
    assert "THE RESULT" in d and r"\fad(300,0)" in d
    pop = lines(doc_for([ov("x", Style(entrance="pop"))]), 5)[0]
    assert r"\fscx62\fscy62" in pop and r"\t(0,260,\fscx100\fscy100)" in pop
    out = lines(doc_for([ov("x", Style(entrance="fade", exit_ms=200))], ), 5)[0]
    assert r"\fad(260,200)" in out


def test_hook_caption_preset_has_outline_and_yellow_highlight():
    d = lines(doc_for([ov("everyone says *X*", Style(preset="hook-caption"))]), 5)[0]
    assert r"\bord" in d and "&H00E6FF&" in d  # #FFE600 highlight (BGR)
    assert r"\3c&H000000&" in d


# -------------------------------------------------- solid / checklist overlays --


def test_chapter_card_recipe_solid_underlay_script_and_sub():
    parts = style_pack.chapter_card(2.0, 5.0, "Step one", "start here", bg="#101010")
    assert [p["kind"] if "kind" in p else "text" for p in parts] == ["solid", "text", "text"]
    doc = doc_for([Overlay.model_validate(p) for p in parts])
    ev = lines(doc)
    assert ev[0].startswith("Dialogue: 0,0:00:02.00,0:00:05.00") and r"\p1" in ev[0]
    assert "&H101010&" in ev[0] and "m 0 0 l 1080 0 l 1080 1920 l 0 1920" in ev[0]
    assert any(r"\fnAllura" in x and "Step one" in x for x in ev)
    assert any("START HERE" in x and r"\fnCormorant Garamond SemiBold" in x for x in ev)
    # Solid sits under every text layer even when it is listed after the text.
    swapped = doc_for([Overlay.model_validate(p) for p in parts[::-1]])
    assert lines(swapped)[0].startswith("Dialogue: 0,")


def test_word_cards_one_word_per_beat_on_flat_background():
    words = [("BUILD", 0.0, 1.0), ("OWN", 1.0, 2.0), ("REPEAT", 2.0, 3.0)]
    parts = style_pack.word_cards(words)
    assert parts[0]["kind"] == "solid" and parts[0]["fill"] == style_pack.CARD_CREAM
    assert (parts[0]["start"], parts[0]["end"]) == (0.0, 3.0)
    doc = doc_for([Overlay.model_validate(p) for p in parts])
    texts = lines(doc, 5)
    assert [t.rsplit("}", 1)[1] for t in texts] == ["BUILD", "OWN", "REPEAT"]
    assert "0:00:01.00,0:00:02.00" in texts[1]


def test_checklist_ticks_row_by_row():
    o = Overlay.model_validate(
        style_pack.checklist(0, 4, ["one", "two", "three"], check_at=[1.0, 2.0, 3.0])[0]
    )
    doc = doc_for([o])
    ev = lines(doc)
    spans = sorted({x.split(",")[1] + "-" + x.split(",")[2] for x in ev})
    assert len(spans) == 4  # 0 / 1 / 2 / 3 ticks done
    ticks = lambda s: [x for x in ev if x.split(",")[1] + "-" + x.split(",")[2] == s and x.startswith("Dialogue: 4") and r"\p1" in x]
    assert [len(ticks(s)) for s in spans] == [0, 1, 2, 3]
    texts = [x for x in ev if x.startswith("Dialogue: 4") and r"\an4" in x]
    assert len(texts) == 12 and all(r"\fnMontserrat Medium" in x for x in texts)
    card = [x for x in ev if x.startswith("Dialogue: 2,") and "&HFAF8FA" not in x]
    assert card and "&HF2F8FA&" in card[0]  # card fill #FAF8F2 (BGR)
    first, last = ev[0], [x for x in ev if x.startswith("Dialogue: 2,")][-1]
    assert r"\fad(220,0)" in first and r"\fad(0,180)" in last


def test_checklist_default_timing_and_fit():
    o = Overlay.model_validate(style_pack.checklist(1, 5, ["a very long checklist row that needs shrinking to fit"] * 2)[0])
    assert o.check_at == [0.8, 1.35]
    ev = [x for x in lines(doc_for([o])) if r"\an4" in x]
    fs = float(re.search(r"\\fs([\d.]+)", ev[0]).group(1))
    full = 46 * font_metrics("Montserrat Medium")["win"]
    assert fs < full


def test_inset_skipped_by_ass_and_validated():
    o = Overlay.model_validate(style_pack.inset(0, 2, "/tmp/x.mp4")[0])
    assert lines(doc_for([o])) == []
    with pytest.raises(ValueError):
        Inset(w=0)
    with pytest.raises(ValueError):
        Inset(radius=0.9)


# -------------------------------------------------------- render integration --


def hsv_sat(a):
    a = a.astype(float)
    return float((a.max(1) - a.min(1)).mean())


def test_look_filter_expressions():
    assert look_filter(Clip(asset_id=1, end=2)) == ""
    assert look_filter(Clip(asset_id=1, end=2, look="bw")) == "hue=s=0,"
    f = look_filter(Clip(asset_id=1, end=2, look="bw", reveal_at=1.5, reveal_dur=0.5))
    assert f == "hue=s='clip((t-1.5)/0.5,0,1)',"


def test_bw_clip_reveals_colour(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "EXPORTS_DIR", tmp_path / "out")
    src = tmp_path / "s.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
         "color=c=0xE03020:s=320x240:r=24:d=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)],
        check=True, env=tool_env(),
    )
    paths = {1: {"path": str(src), "kind": "video"}}
    p = Project(clips=[Clip(asset_id=1, end=3, look="bw", reveal_at=1.2, reveal_dur=0.4)])
    p.export.width = 240
    out = render_snapshot(p.model_dump(), paths, lambda *_: None, _CancelFlag())["video"]
    early = frame(out, 0.5).reshape(-1, 3).astype(int)
    late = frame(out, 2.6).reshape(-1, 3).astype(int)
    video_px = lambda a: a[(a.sum(1) > 40)]  # skip the letterbox bars
    assert hsv_sat(video_px(early)) < 6
    assert hsv_sat(video_px(late)) > 80
    plain = Project(clips=[Clip(asset_id=1, end=3)])
    plain.export.width = 240
    base = render_snapshot(plain.model_dump(), paths, lambda *_: None, _CancelFlag())["video"]
    assert hsv_sat(video_px(frame(base, 0.5).reshape(-1, 3).astype(int))) > 80


def test_inset_clip_and_image_render_over_video(tmp_path, monkeypatch):
    from PIL import Image

    monkeypatch.setattr(config, "EXPORTS_DIR", tmp_path / "out")
    src = tmp_path / "s.mp4"
    fixture_video(src)
    still = tmp_path / "tile.png"
    Image.new("RGB", (64, 64), (250, 20, 20)).save(still)
    paths = {1: {"path": str(src), "kind": "video"}}
    base = Project(clips=[Clip(asset_id=1, end=3)], on_video_text=True)
    base.export.width = 360
    ins = style_pack.inset(0.2, 2.8, still, x=0.25, y=0.4, w=0.5, h=0.2, radius=0.2,
                           anim="none", shadow=False)
    vid = style_pack.inset(0.2, 2.8, src, x=0.1, y=0.05, w=0.3, h=0.15, gray=True,
                           anim="fade", anim_ms=100)
    with_tile = base.model_copy(deep=True)
    with_tile.timeline_overlays = [Overlay.model_validate(o) for o in ins + vid]
    a = frame(render_snapshot(with_tile.model_dump(), paths, lambda *_: None, _CancelFlag())["video"], 1.0).reshape(-1, 360, 3)
    b = frame(render_snapshot(base.model_dump(), paths, lambda *_: None, _CancelFlag())["video"], 1.0).reshape(-1, 360, 3)
    h, w = a.shape[:2]
    cy, cx = int(0.5 * h), int(0.5 * w)
    centre = a[cy - 4 : cy + 4, cx - 4 : cx + 4].reshape(-1, 3).mean(0)
    assert centre[0] > 200 and centre[1] < 60 and centre[2] < 60  # red tile
    corner = a[int(0.41 * h) : int(0.415 * h), int(0.255 * w) : int(0.26 * w)].reshape(-1, 3).mean(0)
    untouched = b[int(0.41 * h) : int(0.415 * h), int(0.255 * w) : int(0.26 * w)].reshape(-1, 3).mean(0)
    assert np.abs(corner - untouched).sum() < 30  # rounded corner leaves the video visible
    tile = a[int(0.055 * h) : int(0.19 * h), int(0.12 * w) : int(0.38 * w)].reshape(-1, 3).astype(int)
    assert np.abs(tile - b[int(0.055 * h) : int(0.19 * h), int(0.12 * w) : int(0.38 * w)].reshape(-1, 3)).sum() > 0
    # Inset only while on_video_text is on (clean master stays clean).
    off = with_tile.model_copy(deep=True)
    off.on_video_text = False
    c = frame(render_snapshot(off.model_dump(), paths, lambda *_: None, _CancelFlag())["video"], 1.0).reshape(-1, 360, 3)
    assert np.array_equal(c, b)


def test_pack_text_and_checklist_render(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "EXPORTS_DIR", tmp_path / "out")
    src = tmp_path / "s.mp4"
    fixture_video(src)
    paths = {1: {"path": str(src), "kind": "video"}}
    items = (
        style_pack.chapter_card(0, 1.5, "Step one", "begin", bg="#102030")
        + style_pack.checklist(1.5, 3, ["alpha", "beta"], check_at=[0.2, 0.6], accent="#00FF00")
    )
    p = project_with([Overlay.model_validate(o) for o in items])
    p.export.width = 360
    out = render_snapshot(p.model_dump(), paths, lambda *_: None, _CancelFlag())["video"]
    card = frame(out, 0.9).reshape(-1, 3).astype(int)
    assert np.abs(card[:50].mean(0) - np.array([16, 32, 48])).max() < 8  # flat card colour
    later = frame(out, 2.5).reshape(-1, 3).astype(int)
    green = ((later[:, 1] > 200) & (later[:, 0] < 60) & (later[:, 2] < 60)).sum()
    assert green > 30  # ticked circles
    white = ((later > 225).all(axis=1)).sum()
    assert white > 500  # the cream card


# ------------------------------------------------------------------- hooks --


def test_hook_library_shape_and_fill():
    lib = hooks.library()
    assert len(lib) == 6
    ids = [h["id"] for h in lib]
    assert len(set(ids)) == 6
    for h in lib:
        assert h["style"] in style_pack.PRESETS
        for line in h["lines"]:
            for slot in re.findall(r"\{(\w+)\}", line):
                assert slot in h["slots"], (h["id"], slot)
        filled = hooks.fill(h)
        assert all("{" not in x for x in filled)
    t = hooks.get("contrast")
    assert hooks.fill(t, {"common_advice": "rush", "hidden_part": "it burns you out"}) == [
        "*rush*",
        "it burns you out",
    ]
    # Slots are text, never markup.
    assert hooks.fill(t, {"common_advice": "a*b*\nc"})[0] == "*ab c*"
    with pytest.raises(ValueError):
        hooks.fill(t, {"nope": "x"})
    with pytest.raises(KeyError):
        hooks.get("missing")


def test_hook_build_overlays_timing_style_and_pattern_interrupt():
    out = hooks.build("contrast", {"hidden_part": "it never comes"}, start=0.5,
                      duration=3.0, accent="#D4AF37")
    assert len(out) == 2
    assert (out[0]["start"], out[0]["end"]) == (0.5, 3.5)
    assert out[1]["start"] == pytest.approx(0.5 + 0.55)  # staggered second line
    assert out[0]["style"]["preset"] == "hook-caption" and out[0]["style"]["highlight"] == "#D4AF37"
    assert out[0]["style"]["size"] == out[1]["style"]["size"]
    assert out[0]["style"]["y"] < out[1]["style"]["y"]
    assert "never comes" in out[1]["text"]
    for o in out:
        Overlay.model_validate(o)
    # Pattern-interrupt insert only when the template defines one and media is given.
    plain = hooks.build("pattern-interrupt", {})
    assert all("kind" not in o for o in plain)
    withmedia = hooks.build("pattern-interrupt", {"pattern": "x"}, start=0, insert_media="/m/cut.mp4")
    assert withmedia[0]["kind"] == "inset"
    assert withmedia[0]["start"] == 1.2 and withmedia[0]["end"] == pytest.approx(1.75)
    assert withmedia[0]["inset"]["w"] == 1 and withmedia[0]["inset"]["radius"] == 0
    optional = hooks.build("contrast", {}, start=0.1, insert_media="/m/cut.mp4")
    assert optional[0]["kind"] == "inset" and optional[0]["start"] == pytest.approx(1.4)
    assert hooks.get("contrast")["insert"]["recommended"] is False
    assert hooks.get("pattern-interrupt")["insert"]["recommended"] is True
    # Hook text is shrunk to fit but shares one size across lines.
    long = hooks.build("contrast", {"common_advice": "x" * 40})
    assert long[0]["style"]["size"] < 84


def test_hook_and_style_pack_api(client):
    r = client.get("/api/editor/hooks").json()
    assert len(r["hooks"]) == 6
    ok = client.post("/api/editor/hooks/contrast/overlays",
                     json={"slots": {"common_advice": "wait"}, "start": 0.2})
    assert ok.status_code == 200
    assert ok.json()["overlays"][0]["text"] == "*wait*"
    assert client.post("/api/editor/hooks/nope/overlays", json={}).status_code == 404
    assert client.post("/api/editor/hooks/contrast/overlays",
                       json={"slots": {"zzz": "1"}}).status_code == 422

    cat = client.get("/api/editor/style-pack").json()
    assert "serif-card" in cat["presets"] and "Montserrat Light" in cat["fonts"]
    assert {"mixed-title", "chapter-card", "word-cards", "checklist", "inset"} <= set(cat["recipes"])
    rec = client.post("/api/editor/style-pack/recipes/chapter-card",
                      json={"params": {"start": 1, "end": 3, "title": "Step one"}})
    assert rec.status_code == 200 and rec.json()["overlays"][0]["kind"] == "solid"
    bad = client.post("/api/editor/style-pack/recipes/chapter-card", json={"params": {"start": 1}})
    assert bad.status_code == 422
    assert client.post("/api/editor/style-pack/recipes/none", json={}).status_code == 404
    # A plan can carry the packed overlays through save -> load unchanged.
    assert Overlay.model_validate(rec.json()["overlays"][1]).style.preset == "chapter-script"
