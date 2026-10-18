"""Hook headline overlay and brand end card."""
import json
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from app.edits import brands, kinetic, render, story
from .test_beat_sync import _approved_story_with, long_assets  # noqa: F401
from .test_story import _capture_ffmpeg, _words, fake_tts  # noqa: F401

pytestmark = pytest.mark.usefixtures("cleanup_assets")


def _beat(start, text, spans, step=0.4):
    words = _words(text, step=step)
    return {"start": start, "words": words, "slot": words[-1]["end"] + 0.3, "emphasis": spans}


def test_brands_config_and_defaults(tmp_path, monkeypatch):
    b = brands.load_brands()
    assert set(b) >= {"albertaweb", "buildwithdb"}
    assert b["albertaweb"]["cta"] == "Run the live scan" and b["buildwithdb"]["url"] == "buildwithdb.ca"
    cfg = tmp_path / "brands.yaml"
    cfg.write_text('x:\n  name: "X Co"\n  colors: ["#112233", "bad"]\n  cta: "Go"\n')
    monkeypatch.setenv("MF_BRANDS_FILE", str(cfg))
    assert brands.get_brand("x")["name"] == "X Co"
    assert brands.get_brand("x")["colors"] == ["#112233", "#112233"]
    with pytest.raises(brands.BrandError):
        brands.get_brand("albertaweb")                       # file replaces the defaults
    assert brands.get_brand(None) is None
    cfg.write_text(": : not yaml [")
    assert "albertaweb" in brands.load_brands()               # unreadable -> built-ins


def test_hook_demotes_early_emphasis_words():
    beats = [_beat(0.0, "We rose above the valley at dawn.", [[4, 5]], step=0.2)]
    without = kinetic.plan_events(beats)
    assert [e["text"] for e in without["emphasis"]] == ["valley"]
    hooked = kinetic.plan_events(beats, hook_end=kinetic.HOOK_SECONDS)   # valley starts at 0.8 s
    assert hooked["emphasis"] == [] and "valley" in [b["text"] for b in hooked["body"]]
    late = kinetic.plan_events(beats, hook_end=0.5)
    assert [e["text"] for e in late["emphasis"]] == ["valley"]


@pytest.mark.parametrize("w,h", [(1080, 1920), (1920, 1080)])
def test_hook_layout_fits_safe_zone(w, h, tmp_path):
    zones = {"top": 0.12, "bottom": 0.22, "left": 0.06, "right": 0.14} if h > w else None
    text = "Stop scrolling: your website is invisible to Google"
    lines, size = kinetic.hook_layout(text, w, h, zones)
    assert 1 <= len(lines) <= 3 and " ".join(lines) == text.upper()
    info = kinetic.render_hook_images(text, w, h, tmp_path, safe_zones=zones)
    sx, sy, sw, sh = kinetic._safe_rect(w, h, zones)
    iw, ih = info["size"]
    assert info["x"] >= sx and info["x"] + iw <= sx + sw
    assert info["y"] == sy and info["y"] + ih <= sy + sh * 0.35      # headline stays in the upper area
    assert Image.open(info["hold"]).size == (iw, ih) and len(info["frames"]) == kinetic.GLITCH_FRAMES - 1


def test_brand_card_assets_geometry(tmp_path):
    zones = {"top": 0.12, "bottom": 0.22, "left": 0.06, "right": 0.14}
    for bid in ("albertaweb", "buildwithdb"):
        brand = brands.get_brand(bid)
        card = kinetic.brand_card_assets(brand, 1080, 1920, tmp_path, zones)
        sx, sy, sw, sh = card["safe"]
        name = card["name"]
        iw, ih = name["size"]
        assert name["x"] >= sx and name["x"] + iw <= sx + sw
        assert sy <= name["y"] and name["y"] + ih < sy + sh
        ys = [t["y"] for t in card["texts"]]
        assert ys == sorted(ys) and ys[0] > name["y"] + ih - 5 and ys[-1] < sy + sh
        assert [t["text"] for t in card["texts"]][0] == brand["cta"]
        lines = kinetic.card_ass_lines(card, 10.0, 11.8)
        assert len(lines) == len(card["texts"]) and "\\fad(250,0)" in lines[0] and "Dialogue: 2,0:00:10.15" in lines[0]
    # dark brand colours are lightened so the gradient reads on the darkened frame
    assert sum(kinetic._legible((0x11, 0x11, 0x11))) > 3 * 100
    assert kinetic._legible((0xC9, 0xA2, 0x27)) == (0xC9, 0xA2, 0x27)
    # the @handle only appears when it differs from the url
    assert len(kinetic.brand_card_assets(brands.get_brand("albertaweb"), 1080, 1920, tmp_path, zones)["texts"]) == 2
    assert len(kinetic.brand_card_assets(brands.get_brand("buildwithdb"), 1080, 1920, tmp_path, zones)["texts"]) == 3


def _probe_duration(path):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                 "-of", "csv=p=0", path], capture_output=True, text=True).stdout)


def test_story_render_with_hook_and_end_card(client, fake_tts, long_assets, monkeypatch):  # noqa: F811
    plan = story.create_story_plan(
        "We rose above the valley at dawn.\n\nThe river cut silver light through trees.",
        search_fn=lambda db, q, limit: long_assets, cloud_approved=True, voice_consent=True,
        hook_text="Stop scrolling", brand="buildwithdb")
    pid = plan["plan_id"]
    sc = story.get_story(pid)
    assert sc["hook_text"] == "Stop scrolling" and sc["brand"]["id"] == "buildwithdb"
    assert client.post(f"/api/edits/plan/{pid}/approve").status_code == 200
    seen = _capture_ffmpeg(monkeypatch)
    out = render.render_plan(pid, ratio="9:16", width=270, height=480)
    graph = seen[-1][seen[-1].index("-filter_complex") + 1]
    assert f"tpad=stop_mode=clone:stop_duration={brands.END_CARD_SECONDS:.3f}" in graph
    assert "boxblur=" in graph and "eq=brightness=-0.30" in graph
    assert "apad=whole_dur=" in graph                                  # voice padded under the card
    assert "between(t,0.000,1.400)" in graph                           # hook window
    # card text rides on the same ASS file as the captions
    assert "ass=filename=" in graph
    assert _probe_duration(out) == pytest.approx(sc["duration"] + brands.END_CARD_SECONDS, abs=0.35)
    assert not (Path(sc["voiceover"]).parent / "emph").exists()


def test_story_render_without_hook_or_brand_is_unchanged(client, fake_tts, long_assets, monkeypatch):  # noqa: F811
    plan = _approved_story_with(client, long_assets)
    seen = _capture_ffmpeg(monkeypatch)
    out = render.render_plan(plan["plan_id"], ratio="9:16", width=270, height=480)
    graph = seen[-1][seen[-1].index("-filter_complex") + 1]
    assert "tpad" not in graph and "boxblur" not in graph and "between(t,0.000,1.400)" not in graph
    assert _probe_duration(out) == pytest.approx(story.get_story(plan["plan_id"])["duration"], abs=0.3)


def test_brand_logo_is_composited_when_present(client, fake_tts, long_assets, tmp_path, monkeypatch):  # noqa: F811
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (200, 100), (255, 0, 0, 255)).save(logo)
    cfg = tmp_path / "brands.yaml"
    cfg.write_text(f'acme:\n  name: "Acme"\n  cta: "Buy"\n  url: "acme.test"\n  colors: ["#FF8800", "#FFFF00"]\n  logo_path: "{logo}"\n')
    monkeypatch.setenv("MF_BRANDS_FILE", str(cfg))
    plan = story.create_story_plan("One short beat about drones.", search_fn=lambda db, q, limit: long_assets,
                                   cloud_approved=True, voice_consent=True, brand="acme")
    client.post(f"/api/edits/plan/{plan['plan_id']}/approve")
    seen = _capture_ffmpeg(monkeypatch)
    out = render.render_plan(plan["plan_id"], ratio="9:16", width=270, height=480)
    graph = seen[-1][seen[-1].index("-filter_complex") + 1]
    assert "scale=" in graph and "fade=t=in" in graph
    assert str(logo) in seen[-1]
    assert Path(out).is_file()
