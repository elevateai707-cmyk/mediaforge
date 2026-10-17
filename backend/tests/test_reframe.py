"""Focused checks for the CPU-light reframe helper."""
from __future__ import annotations

import subprocess
import json


def _video(path):
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi",
        "-i", "color=c=blue:s=320x180:d=1", "-pix_fmt", "yuv420p",
        "-c:v", "libx264", "-preset", "ultrafast", str(path),
    ], check=True, capture_output=True)


def test_center_analysis_writes_sidecar_and_has_bounded_path(tmp_path):
    from app.edits import reframe

    source = tmp_path / "landscape.mp4"
    _video(source)
    result = reframe.analyze_reframe(str(source), start=0, end=1, mode="center",
                                     safe_zones={"top": .2, "bottom": .3, "right": .1})
    assert result["source"] == "center"
    assert result["safe_zones"] == {"top": .2, "bottom": .3, "left": 0.0, "right": .1}
    assert len(result["path_points"]) == 5
    assert all(point["x"] == .5 and point["y"] == .5 for point in result["path_points"])
    assert result.get("sidecar")


def test_crop_filter_and_preview_stills(tmp_path):
    from app.edits import reframe

    source = tmp_path / "landscape.mp4"
    _video(source)
    analysis = reframe.analyze_reframe(str(source), start=0, end=1, mode="center")
    filt = reframe.build_crop_filter(analysis, 180, 320, 1)
    assert filt.startswith("crop=180:320:")
    assert "eval=" not in filt
    x, y, w, h = reframe.crop_box(analysis, 180, 320)
    assert (x, y) == (110, 0)
    assert (w, h) == (101, 180)
    previews = reframe.generate_preview_stills(source, analysis, tmp_path / "previews",
                                               target_width=180, target_height=320)
    assert len(previews) == 3
    assert all((tmp_path / "previews" / f"reframe_{i}.jpg").is_file() for i in (1, 2, 3))


def test_crop_path_interpolates_midpoint_move_and_return():
    """Render and preview both visit the middle pan point, then return."""
    from app.edits import reframe

    analysis = {
        "source_size": {"width": 1000, "height": 500},
        "path_points": [
            {"t": 0.0, "x": .2, "y": .5},
            {"t": .5, "x": .8, "y": .5},
            {"t": 1.0, "x": .2, "y": .5},
        ],
    }
    start = reframe.crop_box(analysis, 100, 200, 0.0)
    middle = reframe.crop_box(analysis, 100, 200, 0.5)
    returning = reframe.crop_box(analysis, 100, 200, .75)
    assert middle[0] > start[0]
    assert start[0] < returning[0] < middle[0]
    graph = reframe.build_crop_filter(analysis, 100, 200, 4)
    # Two half-duration segments (0→2s and 2→4s), rather than one flat
    # first-to-last interpolation, are present in the ffmpeg expression.
    assert "if(lt(t,2.00000)" in graph and "if(lt(t,4.00000)" in graph


def test_large_video_face_boxes_are_scaled_from_detector_frame(monkeypatch, tmp_path):
    """Video faces are stored from a 960px extraction, not the 4K source."""
    from PIL import Image
    from app import config
    from app.edits import reframe

    monkeypatch.setattr(config, "THUMBS_DIR", tmp_path / "thumbs")
    face_dir = config.THUMBS_DIR / "77"
    face_dir.mkdir(parents=True)
    Image.new("RGB", (960, 540)).save(face_dir / "face_25.jpg")

    class Rows:
        def all(self):
            return [(json.dumps([720, 180, 840, 360]), .25, "video")]

    class Db:
        def execute(self, *_args, **_kwargs):
            return Rows()

    points = reframe._face_points(77, 0, 1, 3840, 2160, db=Db())
    assert points == [(.25, 3120.0, 1080.0)]
    path = reframe._smooth(points, 0, 1, 3840, 2160)
    assert path[0]["x"] == .8125
    assert path[0]["y"] == .5


def test_rotation_uses_display_dimensions_for_face_normalization(monkeypatch, tmp_path):
    """A rotated asset and its auto-rotated face frame share display space."""
    from PIL import Image
    from app import config
    from app.edits import reframe

    class Probe:
        returncode = 0
        stdout = json.dumps({"streams": [{"width": 3840, "height": 2160,
                                            "side_data_list": [{"rotation": 90}]}],
                             "format": {"duration": "1"}})

    monkeypatch.setattr(reframe.subprocess, "run", lambda *_args, **_kwargs: Probe())
    assert reframe._probe_size("rotated.mp4") == (2160, 3840, 1.0)

    monkeypatch.setattr(config, "THUMBS_DIR", tmp_path / "thumbs")
    face_dir = config.THUMBS_DIR / "88"
    face_dir.mkdir(parents=True)
    Image.new("RGB", (540, 960)).save(face_dir / "face_50.jpg")

    class Rows:
        def all(self):
            return [(json.dumps([216, 384, 324, 576]), .5, "video")]

    class Db:
        def execute(self, *_args, **_kwargs):
            return Rows()

    point = reframe._face_points(88, 0, 1, 2160, 3840, db=Db())[0]
    assert point == (.5, 1080.0, 1920.0)
